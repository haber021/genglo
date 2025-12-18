import json
import io
import csv
from datetime import timedelta, datetime
from decimal import Decimal, InvalidOperation

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from functools import wraps
from django.db.models import Sum, Count, Avg, Q, F
from django.db import transaction
from django.db.models.functions import TruncDate
from django.core.paginator import Paginator
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.views.decorators.http import require_http_methods
from django.conf import settings
from django.template.loader import render_to_string

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

from inventory.models import Product, Category
from members.models import Member, MemberType, BalanceTransaction, DeletedMember
from transactions.models import Transaction, TransactionItem
from admin_panel.utils import get_admin_email
from django.core.mail import EmailMessage


def handle_login(request, redirect_to_dashboard=False):
    """Shared login logic that routes admin and regular users appropriately"""
    if request.user.is_authenticated:
        # Check if user is admin and redirect accordingly
        if is_admin_user(request.user):
            return redirect('dashboard')
        else:
            return redirect('user_choice')
    
    # Check if member session exists (for members without Django user account)
    member_id = request.session.get('member_id')
    if member_id:
        try:
            member = Member.objects.get(id=member_id, is_active=True)
            # Only allow members with role "member" without username
            if member.role == 'member' and (member.user is None or not member.user.username):
                return redirect('user_choice')
        except Member.DoesNotExist:
            pass
    
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        
        if not username or not password:
            messages.error(request, 'Please enter both username and password.')
            return render(request, 'admin_panel/login.html')
        
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            
            # Check if user is admin (staff/superuser or member with admin role)
            next_url = request.POST.get('next') or request.GET.get('next')
            if is_admin_user(user):
                # Admin users go to dashboard
                if next_url == 'dashboard':
                    return redirect('dashboard')
                if next_url and next_url.startswith('/') and next_url != '/admin/':
                    return redirect(next_url)
                return redirect('dashboard')
            else:
                # Regular users go to choice page (or next URL if provided)
                messages.success(request, f'Welcome back, {user.get_full_name() or user.username}!')
                if next_url and next_url.startswith('/') and next_url != '/admin/':
                    return redirect(next_url)
                return redirect('user_choice')
        else:
            # Fallback: Check if this is a member with role "member" without username
            # Try to find member by RFID (username field) and verify PIN (password field)
            if password.isdigit() and len(password) == 4:  # PIN is 4 digits
                try:
                    member = Member.objects.get(
                        rfid_card_number=username,
                        is_active=True,
                        role='member'
                    )
                    # Only allow if member doesn't have a username
                    if (member.user is None or not member.user.username) and member.check_pin(password):
                        # Store member info in session for members without username
                        request.session['member_id'] = member.id
                        request.session['member_rfid'] = member.rfid_card_number
                        request.session['member_role'] = member.role
                        
                        # Redirect to user choice page
                        next_url = request.POST.get('next') or request.GET.get('next')
                        messages.success(request, f'Welcome back, {member.full_name}!')
                        if next_url and next_url.startswith('/') and next_url != '/admin/':
                            return redirect(next_url)
                        return redirect('user_choice')
                except Member.DoesNotExist:
                    pass
                except Exception:
                    pass
            
            messages.error(request, 'Invalid username or password. Please try again.')
    
    return render(request, 'admin_panel/login.html')


@require_http_methods(["GET", "POST"])
def root_login(request):
    """Root login page - first page users see"""
    return handle_login(request)


@require_http_methods(["GET", "POST"])
def redirect_to_root_login(request):
    """Redirect /admin/login/ to root login page, preserving query parameters"""
    from django.urls import reverse
    
    # Build the root login URL
    root_login_url = reverse('root_login')
    
    # Preserve query parameters if they exist
    query_string = request.META.get('QUERY_STRING', '')
    if query_string:
        root_login_url = f"{root_login_url}?{query_string}"
    
    return redirect(root_login_url)


def can_access_django_admin(user):
    """Check if a user can access Django admin (superuser or linked to Member with admin role)
    Staff users (user.is_staff but not superuser) are NOT allowed to access Django admin.
    Users with Member role 'staff' are also NOT allowed to access Django admin.
    """
    # Only superusers can access Django admin (not regular staff)
    if user.is_superuser:
        return True
    
    # Block Django staff users (is_staff but not superuser)
    if user.is_staff and not user.is_superuser:
        return False
    
    # Check if user is linked to a Member
    try:
        member = Member.objects.get(user=user)
        # Block users with Member role 'staff'
        if member.role == 'staff' and member.is_active:
            return False
        # Allow users with Member role 'admin'
        if member.role == 'admin' and member.is_active:
            return True
    except Member.DoesNotExist:
        pass
    except Exception:
        pass
    
    return False


def is_admin_user(user):
    """Check if a user is an admin for dashboard access (superuser or linked to Member with admin role)
    Staff users (user.is_staff but not superuser) are NOT considered admin for dashboard access.
    """
    # Only superusers are considered admin for dashboard
    if user.is_superuser:
        return True
    
    # Check if user is linked to a Member with admin role
    try:
        member = Member.objects.get(user=user)
        if member.role == 'admin' and member.is_active:
            return True
    except Member.DoesNotExist:
        pass
    except Exception:
        pass
    
    return False


def is_cashier_or_admin(user):
    """Check if a user is a cashier or admin (staff/superuser or linked to Member with cashier/admin role)"""
    if user.is_staff or user.is_superuser:
        return True
    
    # Check if user is linked to a Member with cashier or admin role
    try:
        member = Member.objects.get(user=user)
        if member.role in ['cashier', 'admin'] and member.is_active:
            return True
    except Member.DoesNotExist:
        pass
    except Exception:
        pass
    
    return False


def member_or_login_required(view_func):
    """Decorator that allows access if user is authenticated OR if member session exists"""
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        # Check if Django user is authenticated
        if request.user.is_authenticated:
            return view_func(request, *args, **kwargs)
        
        # Check if member session exists (for members without Django user account)
        member_id = request.session.get('member_id')
        if member_id:
            try:
                member = Member.objects.get(id=member_id, is_active=True)
                # Only allow members with role "member" without username
                if member.role == 'member' and (member.user is None or not member.user.username):
                    return view_func(request, *args, **kwargs)
            except Member.DoesNotExist:
                pass
        
        # Not authenticated, redirect to login
        messages.warning(request, 'Please log in to access this page.')
        return redirect('root_login')
    
    return _wrapped_view


def is_staff_user(user):
    """Check if a user is a staff member (not admin or cashier)"""
    # If user is staff/superuser, they're not a regular staff member
    if user.is_staff or user.is_superuser:
        return False
    
    # Check if user is linked to a Member with staff role
    try:
        member = Member.objects.get(user=user)
        if member.role == 'staff' and member.is_active:
            return True
    except Member.DoesNotExist:
        pass
    except Exception:
        pass
    
    return False


def is_staff_role(user):
    """Check if a user has staff role (Django staff or Member role 'staff')
    This includes both Django staff users and Member role 'staff' users.
    """
    # Check if user is Django staff (but not superuser)
    if user.is_staff and not user.is_superuser:
        return True
    
    # Check if user is linked to a Member with staff role
    try:
        member = Member.objects.get(user=user)
        if member.role == 'staff' and member.is_active:
            return True
    except Member.DoesNotExist:
        pass
    except Exception:
        pass
    
    return False


@login_required
def dashboard(request):
    # Ensure only admin users can access dashboard
    if not is_admin_user(request.user):
        messages.warning(request, 'You do not have permission to access the admin dashboard.')
        return redirect('kiosk_home')
    today = timezone.now().date()
    two_weeks_ago = today - timedelta(days=13)
    month_ago = today - timedelta(days=30)

    base_qs = Transaction.objects.filter(status='completed')

    total_transactions = base_qs.count()
    total_revenue = base_qs.aggregate(Sum('total_amount'))['total_amount__sum'] or 0
    today_transactions = base_qs.filter(created_at__date=today).count()
    today_revenue = base_qs.filter(created_at__date=today).aggregate(Sum('total_amount'))['total_amount__sum'] or 0

    total_members = Member.objects.filter(is_active=True).count()

    low_stock_products = Product.objects.filter(is_active=True, stock_quantity__lte=10).count()
    out_of_stock_products = Product.objects.filter(is_active=True, stock_quantity=0).count()

    recent_transactions = base_qs.order_by('-created_at')[:10]
    top_products = TransactionItem.objects.filter(transaction__status='completed').values('product_name').annotate(
        total_sold=Sum('quantity'),
        total_revenue=Sum('total_price')
    ).order_by('-total_sold')[:10]

    # --- Chart data calculations ---
    daily_sales_raw = base_qs.filter(created_at__date__gte=two_weeks_ago).annotate(
        day=TruncDate('created_at')
    ).values('day').annotate(
        total=Sum('total_amount')
    ).order_by('day')

    daily_sales_map = {entry['day']: float(entry['total'] or 0) for entry in daily_sales_raw}
    daily_labels = []
    daily_totals = []
    for offset in range(14):
        day = two_weeks_ago + timedelta(days=offset)
        daily_labels.append(day.strftime('%b %d'))
        daily_totals.append(round(daily_sales_map.get(day, 0), 2))

    payment_breakdown = base_qs.values('payment_method').annotate(
        total=Sum('total_amount')
    )
    payment_label_map = dict(Transaction.PAYMENT_METHODS)
    payment_labels = []
    payment_totals = []
    for entry in payment_breakdown:
        label = payment_label_map.get(entry['payment_method'], entry['payment_method'].title())
        payment_labels.append(label)
        payment_totals.append(float(entry['total'] or 0))

    category_sales = TransactionItem.objects.filter(
        transaction__status='completed',
        product__category__isnull=False
    ).values('product__category__name').annotate(
        total=Sum('total_price')
    ).order_by('-total')[:6]
    category_labels = [entry['product__category__name'] or 'Uncategorized' for entry in category_sales]
    category_totals = [float(entry['total'] or 0) for entry in category_sales]

    top_members = Member.objects.filter(
        transactions__status='completed'
    ).annotate(
        total_spent=Sum('transactions__total_amount')
    ).order_by('-total_spent')[:5]

    # --- Refund statistics ---
    # Refunds are identified by: status='cancelled' AND notes contains 'Refund'
    # When a refund is processed, the transaction status is set to 'cancelled' and notes contain 'Refunded'
    # Also check BalanceTransaction records to catch any refunds that might have different note formats
    import re
    
    # Get transaction numbers from BalanceTransaction records with "Refund" in notes
    refund_balance_txns = BalanceTransaction.objects.filter(
        notes__icontains='Refund'
    ).values_list('notes', flat=True)
    
    # Extract transaction numbers from balance transaction notes
    refund_txn_numbers = set()
    for note in refund_balance_txns:
        # Match patterns like "Refund for transaction TXN-123" or "Refund for transaction TXN123"
        matches = re.findall(r'transaction\s+([A-Z0-9-]+)', note, re.IGNORECASE)
        refund_txn_numbers.update(matches)
    
    # Query for refunds: cancelled transactions with 'Refund' in notes OR transactions with numbers from balance records
    refund_qs = Transaction.objects.filter(
        Q(status='cancelled', notes__icontains='Refund') |
        Q(transaction_number__in=refund_txn_numbers)
    ).distinct()
    
    total_refunds = refund_qs.count()
    total_refund_amount = refund_qs.aggregate(Sum('total_amount'))['total_amount__sum'] or 0
    today_refunds = refund_qs.filter(updated_at__date=today).count()
    today_refund_amount = refund_qs.filter(updated_at__date=today).aggregate(Sum('total_amount'))['total_amount__sum'] or 0
    
    # Recent refunds
    recent_refunds = refund_qs.order_by('-updated_at')[:10]
    
    # Daily refund trend (14 days)
    daily_refunds_raw = refund_qs.filter(updated_at__date__gte=two_weeks_ago).annotate(
        day=TruncDate('updated_at')
    ).values('day').annotate(
        total=Sum('total_amount'),
        count=Count('id')
    ).order_by('day')
    
    daily_refunds_map = {entry['day']: {'amount': float(entry['total'] or 0), 'count': entry['count']} for entry in daily_refunds_raw}
    daily_refund_labels = []
    daily_refund_amounts = []
    daily_refund_counts = []
    for offset in range(14):
        day = two_weeks_ago + timedelta(days=offset)
        daily_refund_labels.append(day.strftime('%b %d'))
        refund_data = daily_refunds_map.get(day, {'amount': 0, 'count': 0})
        daily_refund_amounts.append(round(refund_data['amount'], 2))
        daily_refund_counts.append(refund_data['count'])

    # Check if user's Member role is 'staff'
    is_member_staff = False
    try:
        member = Member.objects.get(user=request.user)
        if member.role == 'staff' and member.is_active:
            is_member_staff = True
    except Member.DoesNotExist:
        pass

    context = {
        'total_transactions': total_transactions,
        'total_revenue': total_revenue,
        'today_transactions': today_transactions,
        'today_revenue': today_revenue,
        'total_members': total_members,
        'low_stock_products': low_stock_products,
        'out_of_stock_products': out_of_stock_products,
        'recent_transactions': recent_transactions,
        'top_products': top_products,
        'top_members': top_members,
        'daily_sales_labels': json.dumps(daily_labels),
        'daily_sales_totals': json.dumps(daily_totals),
        'payment_labels': json.dumps(payment_labels),
        'payment_totals': json.dumps(payment_totals),
        'category_labels': json.dumps(category_labels),
        'category_totals': json.dumps(category_totals),
        'user_display_name': request.user.get_full_name() or request.user.username,
        'is_admin': is_admin_user(request.user),  # Explicit admin flag for template
        'is_staff_only': request.user.is_staff and not request.user.is_superuser,  # Check if user is staff but not superuser
        'is_member_staff': is_member_staff,  # Check if user's Member role is 'staff'
        # Refund statistics
        'total_refunds': total_refunds,
        'total_refund_amount': total_refund_amount,
        'today_refunds': today_refunds,
        'today_refund_amount': today_refund_amount,
        'recent_refunds': recent_refunds,
        'daily_refund_labels': json.dumps(daily_refund_labels),
        'daily_refund_amounts': json.dumps(daily_refund_amounts),
        'daily_refund_counts': json.dumps(daily_refund_counts),
    }

    return render(request, 'admin_panel/dashboard.html', context)


@login_required
def inventory_management(request):
    if not is_admin_user(request.user):
        messages.warning(request, 'You do not have permission to access this page.')
        return redirect('kiosk_home')
    
    # Get search query and filter from request
    search_query = request.GET.get('search', '').strip()
    filter_type = request.GET.get('filter', 'all')  # 'all', 'low_stock', 'out_of_stock'
    
    # Start with all products
    products = Product.objects.all()
    
    # Apply filter
    if filter_type == 'low_stock':
        # Low stock: stock <= threshold but > 0
        products = products.filter(is_active=True, stock_quantity__lte=F('low_stock_threshold'), stock_quantity__gt=0)
    elif filter_type == 'out_of_stock':
        # Out of stock: stock = 0
        products = products.filter(is_active=True, stock_quantity=0)
    
    # Apply search filter if query exists
    if search_query:
        search_filters = (
            Q(name__icontains=search_query) |
            Q(barcode__icontains=search_query) |
            Q(description__icontains=search_query) |
            Q(category__name__icontains=search_query)
        )
        products = products.filter(search_filters)
    
    # Order by name
    products = products.order_by('name')
    
    # Pagination: 10 products per page
    paginator = Paginator(products, 10)
    page_number = request.GET.get('page', 1)
    try:
        products_page = paginator.get_page(page_number)
    except:
        products_page = paginator.get_page(1)
    
    categories = Category.objects.all()
    
    # Calculate statistics (from all products, not filtered)
    all_products = Product.objects.all()
    total_products = all_products.count()
    low_stock_products = all_products.filter(is_active=True, stock_quantity__lte=F('low_stock_threshold'), stock_quantity__gt=0).count()
    out_of_stock_products = all_products.filter(is_active=True, stock_quantity=0).count()
    total_categories = categories.count()
    
    context = {
        'products': products_page,
        'page_obj': products_page,
        'categories': categories,
        'total_products': total_products,
        'low_stock_products': low_stock_products,
        'out_of_stock_products': out_of_stock_products,
        'total_categories': total_categories,
        'search_query': search_query,
        'filter_type': filter_type,
    }
    
    return render(request, 'admin_panel/inventory.html', context)


@login_required
@require_http_methods(["POST"])
def api_create_product(request):
    """Create a product without using the Django admin UI"""
    if not is_admin_user(request.user):
        return JsonResponse({'success': False, 'error': 'Permission denied'}, status=403)

    try:
        data = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON payload'}, status=400)

    name = (data.get('name') or '').strip()
    barcode = (data.get('barcode') or '').strip()
    description = (data.get('description') or '').strip()
    category_id = data.get('category_id')
    is_active = bool(data.get('is_active', True))

    if not name:
        return JsonResponse({'success': False, 'error': 'Product name is required'}, status=400)
    if not barcode:
        return JsonResponse({'success': False, 'error': 'Barcode is required'}, status=400)
    if Product.objects.filter(barcode=barcode).exists():
        return JsonResponse({'success': False, 'error': 'A product with this barcode already exists'}, status=400)

    try:
        price = Decimal(str(data.get('price', '0')))
        cost = Decimal(str(data.get('cost', '0'))) if data.get('cost') is not None else Decimal('0.00')
    except (InvalidOperation, TypeError):
        return JsonResponse({'success': False, 'error': 'Invalid price or cost value'}, status=400)

    try:
        stock_quantity = int(data.get('stock_quantity', 0))
        low_stock_threshold = int(data.get('low_stock_threshold', 10))
    except (TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Stock quantities must be whole numbers'}, status=400)

    category = None
    if category_id:
        try:
            category = Category.objects.get(id=category_id)
        except Category.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Selected category does not exist'}, status=400)

    product = Product.objects.create(
        name=name,
        barcode=barcode,
        description=description,
        category=category,
        price=price,
        cost=cost,
        stock_quantity=stock_quantity,
        low_stock_threshold=low_stock_threshold,
        is_active=is_active,
    )

    return JsonResponse({
        'success': True,
        'message': 'Product created successfully',
        'product': {
            'id': product.id,
            'name': product.name,
            'barcode': product.barcode,
            'price': str(product.price),
            'stock_quantity': product.stock_quantity,
            'category': product.category.name if product.category else None,
            'is_active': product.is_active,
        }
    })


@login_required
@require_http_methods(["POST"])
def api_create_category(request):
    """Create a category without using the Django admin UI"""
    if not is_admin_user(request.user):
        return JsonResponse({'success': False, 'error': 'Permission denied'}, status=403)

    try:
        data = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON payload'}, status=400)

    name = (data.get('name') or '').strip()
    description = (data.get('description') or '').strip()
    is_active = bool(data.get('is_active', True))

    if not name:
        return JsonResponse({'success': False, 'error': 'Category name is required'}, status=400)

    category = Category.objects.create(
        name=name,
        description=description,
        is_active=is_active,
    )

    return JsonResponse({
        'success': True,
        'message': 'Category created successfully',
        'category': {
            'id': category.id,
            'name': category.name,
            'description': category.description,
            'is_active': category.is_active,
        }
    })


@login_required
@require_http_methods(["POST"])
def api_update_product(request):
    """Update a product without using the Django admin UI"""
    if not is_admin_user(request.user):
        return JsonResponse({'success': False, 'error': 'Permission denied'}, status=403)

    try:
        data = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON payload'}, status=400)

    product_id = data.get('id')
    if not product_id:
        return JsonResponse({'success': False, 'error': 'Product ID is required'}, status=400)

    try:
        product = Product.objects.get(id=product_id)
    except Product.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Product not found'}, status=404)

    name = (data.get('name') or '').strip()
    barcode = (data.get('barcode') or '').strip()
    description = (data.get('description') or '').strip()
    category_id = data.get('category_id')
    is_active = bool(data.get('is_active', True))

    if not name:
        return JsonResponse({'success': False, 'error': 'Product name is required'}, status=400)
    if not barcode:
        return JsonResponse({'success': False, 'error': 'Barcode is required'}, status=400)
    
    # Check if barcode is already used by another product
    if Product.objects.filter(barcode=barcode).exclude(id=product_id).exists():
        return JsonResponse({'success': False, 'error': 'A product with this barcode already exists'}, status=400)

    try:
        price = Decimal(str(data.get('price', '0')))
        cost = Decimal(str(data.get('cost', '0'))) if data.get('cost') is not None else Decimal('0.00')
    except (InvalidOperation, TypeError):
        return JsonResponse({'success': False, 'error': 'Invalid price or cost value'}, status=400)

    try:
        stock_quantity = int(data.get('stock_quantity', 0))
        low_stock_threshold = int(data.get('low_stock_threshold', 10))
    except (TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Stock quantities must be whole numbers'}, status=400)

    category = None
    if category_id:
        try:
            category = Category.objects.get(id=category_id)
        except Category.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Selected category does not exist'}, status=400)

    # Update product
    product.name = name
    product.barcode = barcode
    product.description = description
    product.category = category
    product.price = price
    product.cost = cost
    product.stock_quantity = stock_quantity
    product.low_stock_threshold = low_stock_threshold
    product.is_active = is_active
    product.save()

    return JsonResponse({
        'success': True,
        'message': 'Product updated successfully',
        'product': {
            'id': product.id,
            'name': product.name,
            'barcode': product.barcode,
            'price': str(product.price),
            'stock_quantity': product.stock_quantity,
            'category': product.category.name if product.category else None,
            'is_active': product.is_active,
        }
    })


@login_required
@require_http_methods(["POST"])
def api_update_category(request):
    """Update a category without using the Django admin UI"""
    if not is_admin_user(request.user):
        return JsonResponse({'success': False, 'error': 'Permission denied'}, status=403)

    try:
        data = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON payload'}, status=400)

    category_id = data.get('id')
    if not category_id:
        return JsonResponse({'success': False, 'error': 'Category ID is required'}, status=400)

    try:
        category = Category.objects.get(id=category_id)
    except Category.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Category not found'}, status=404)

    name = (data.get('name') or '').strip()
    description = (data.get('description') or '').strip()
    is_active = bool(data.get('is_active', True))

    if not name:
        return JsonResponse({'success': False, 'error': 'Category name is required'}, status=400)

    # Update category
    category.name = name
    category.description = description
    category.is_active = is_active
    category.save()

    return JsonResponse({
        'success': True,
        'message': 'Category updated successfully',
        'category': {
            'id': category.id,
            'name': category.name,
            'description': category.description,
            'is_active': category.is_active,
        }
    })


@login_required
@require_http_methods(["POST"])
def api_create_member_type(request):
    """Create a member type without the Django admin UI."""
    if not is_admin_user(request.user):
        return JsonResponse({'success': False, 'error': 'Permission denied'}, status=403)

    try:
        data = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON payload'}, status=400)

    name = (data.get('name') or '').strip()
    description = (data.get('description') or '').strip()
    is_active = bool(data.get('is_active', True))

    if not name:
        return JsonResponse({'success': False, 'error': 'Name is required'}, status=400)

    member_type = MemberType.objects.create(
        name=name,
        description=description,
        is_active=is_active,
    )

    return JsonResponse({
        'success': True,
        'message': 'Member type created successfully',
        'member_type': {
            'id': member_type.id,
            'name': member_type.name,
            'description': member_type.description,
            'is_active': member_type.is_active,
        }
    })


@login_required
@require_http_methods(["POST"])
def api_update_member_type(request):
    """Update a member type without the Django admin UI."""
    if not is_admin_user(request.user):
        return JsonResponse({'success': False, 'error': 'Permission denied'}, status=403)

    try:
        data = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON payload'}, status=400)

    member_type_id = data.get('id')
    if not member_type_id:
        return JsonResponse({'success': False, 'error': 'Member type ID is required'}, status=400)

    try:
        member_type = MemberType.objects.get(id=member_type_id)
    except MemberType.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Member type not found'}, status=404)

    name = (data.get('name') or '').strip()
    description = (data.get('description') or '').strip()
    is_active = bool(data.get('is_active', member_type.is_active))

    if not name:
        return JsonResponse({'success': False, 'error': 'Name is required'}, status=400)

    # Update the member type
    member_type.name = name
    member_type.description = description
    member_type.is_active = is_active
    member_type.save()

    return JsonResponse({
        'success': True,
        'message': 'Member type updated successfully',
        'member_type': {
            'id': member_type.id,
            'name': member_type.name,
            'description': member_type.description,
            'is_active': member_type.is_active,
        }
    })


@login_required
@require_http_methods(["POST"])
def api_create_member(request):
    """Create a member without redirecting to the admin site."""
    if not is_admin_user(request.user):
        return JsonResponse({'success': False, 'error': 'Permission denied'}, status=403)

    try:
        data = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON payload'}, status=400)

    first_name = (data.get('first_name') or '').strip()
    last_name = (data.get('last_name') or '').strip()
    rfid = (data.get('rfid') or '').strip()
    email = (data.get('email') or '').strip() or None
    phone = (data.get('phone') or '').strip()
    member_type_id = data.get('member_type_id')
    role = (data.get('role') or 'member').strip() or 'member'
    is_active = bool(data.get('is_active', True))

    # If user is staff, restrict role to 'member' only
    if is_staff_user(request.user):
        if role not in ['member']:
            return JsonResponse({'success': False, 'error': 'Staff members can only create members with "member" role'}, status=403)
        role = 'member'  # Force to member role

    if not first_name or not last_name:
        return JsonResponse({'success': False, 'error': 'First and last name are required'}, status=400)
    if not rfid:
        return JsonResponse({'success': False, 'error': 'RFID card number is required'}, status=400)
    if Member.objects.filter(rfid_card_number=rfid).exists():
        return JsonResponse({'success': False, 'error': 'RFID card number already exists'}, status=400)
    if email and Member.objects.filter(email=email).exists():
        return JsonResponse({'success': False, 'error': 'Email already exists'}, status=400)

    member_type = None
    if member_type_id:
        try:
            member_type = MemberType.objects.get(id=member_type_id)
        except MemberType.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Selected member type does not exist'}, status=400)

    member = Member.objects.create(
        first_name=first_name,
        last_name=last_name,
        rfid_card_number=rfid,
        email=email,
        phone=phone,
        member_type=member_type,
        role=role if role in dict(Member.ROLE_CHOICES) else 'member',
        is_active=is_active,
    )

    # Handle user account creation if requested
    create_user_account = data.get('create_user_account', False)
    if create_user_account:
        username = (data.get('username') or '').strip()
        password = data.get('password', '').strip()
        
        if not username:
            # Delete member if user creation fails
            member.delete()
            return JsonResponse({'success': False, 'error': 'Username is required when creating a user account'}, status=400)
        
        if not password:
            member.delete()
            return JsonResponse({'success': False, 'error': 'Password is required when creating a user account'}, status=400)
        
        # Check if username already exists
        if User.objects.filter(username=username).exists():
            member.delete()
            return JsonResponse({'success': False, 'error': f'Username "{username}" already exists'}, status=400)
        
        # Create User account
        try:
            user = User.objects.create_user(
                username=username,
                email=email or '',
                password=password,
                first_name=first_name,
                last_name=last_name,
            )
            # Link user to member
            member.user = user
            member.save()
        except Exception as e:
            member.delete()
            return JsonResponse({'success': False, 'error': f'Failed to create user account: {str(e)}'}, status=400)

    return JsonResponse({
        'success': True,
        'message': 'Member created successfully',
        'member': {
            'id': member.id,
            'name': member.full_name,
            'rfid': member.rfid_card_number,
            'email': member.email or '',
            'phone': member.phone,
            'member_type': member.member_type.name if member.member_type else '',
            'role': member.role,
            'is_active': member.is_active,
            'balance': str(member.balance),
        }
    })


@login_required
@require_http_methods(["POST"])
def api_update_member(request):
    """Update a member without redirecting to the admin site."""
    if not is_admin_user(request.user):
        return JsonResponse({'success': False, 'error': 'Permission denied'}, status=403)

    try:
        data = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON payload'}, status=400)

    member_id = data.get('member_id')
    if not member_id:
        return JsonResponse({'success': False, 'error': 'Member ID is required'}, status=400)

    try:
        member = Member.objects.get(id=member_id)
    except Member.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Member not found'}, status=404)

    first_name = (data.get('first_name') or member.first_name).strip()
    last_name = (data.get('last_name') or member.last_name).strip()
    rfid = (data.get('rfid') or member.rfid_card_number).strip()
    email = (data.get('email') or '').strip() or None
    phone = (data.get('phone') or member.phone).strip()
    member_type_id = data.get('member_type_id')
    role = (data.get('role') or member.role).strip()
    is_active = bool(data.get('is_active', member.is_active))

    # If user is staff, restrict role changes
    if is_staff_user(request.user):
        # Staff can only set role to 'member'
        # If member already has admin/cashier/staff role, keep it (staff can't change it)
        if member.role in ['admin', 'cashier', 'staff']:
            role = member.role  # Keep existing role, don't allow change
        elif role not in ['member']:
            return JsonResponse({'success': False, 'error': 'Staff members can only set role to "member"'}, status=403)
        else:
            role = 'member'  # Force to member role

    if not first_name or not last_name:
        return JsonResponse({'success': False, 'error': 'First and last name are required'}, status=400)
    if not rfid:
        return JsonResponse({'success': False, 'error': 'RFID card number is required'}, status=400)

    if Member.objects.filter(rfid_card_number=rfid).exclude(id=member.id).exists():
        return JsonResponse({'success': False, 'error': 'RFID card number already exists'}, status=400)
    if email and Member.objects.filter(email=email).exclude(id=member.id).exists():
        return JsonResponse({'success': False, 'error': 'Email already exists'}, status=400)

    member_type = None
    if member_type_id:
        try:
            member_type = MemberType.objects.get(id=member_type_id)
        except MemberType.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Selected member type does not exist'}, status=400)

    member.first_name = first_name
    member.last_name = last_name
    member.rfid_card_number = rfid
    member.email = email
    member.phone = phone
    member.member_type = member_type
    member.role = role if role in dict(Member.ROLE_CHOICES) else member.role
    member.is_active = is_active
    
    # Handle user account creation/update if requested
    create_user_account = data.get('create_user_account', False)
    if create_user_account:
        username = (data.get('username') or '').strip()
        password = data.get('password', '').strip()
        
        if not username:
            return JsonResponse({'success': False, 'error': 'Username is required when creating a user account'}, status=400)
        
        # Check if member already has a user account
        if member.user:
            # Update existing user account
            user = member.user
            # Check if username is being changed and if new username is available
            if user.username != username:
                if User.objects.filter(username=username).exclude(id=user.id).exists():
                    return JsonResponse({'success': False, 'error': f'Username "{username}" already exists'}, status=400)
                user.username = username
            
            user.first_name = first_name
            user.last_name = last_name
            user.email = email or ''
            # Update password only if provided
            if password:
                user.set_password(password)
            user.save()
        else:
            # Create new user account - use transaction to ensure atomicity
            if not password:
                return JsonResponse({'success': False, 'error': 'Password is required when creating a new user account'}, status=400)
            
            # Check if username already exists
            if User.objects.filter(username=username).exists():
                return JsonResponse({'success': False, 'error': f'Username "{username}" already exists'}, status=400)
            
            try:
                user = User.objects.create_user(
                    username=username,
                    email=email or '',
                    password=password,
                    first_name=first_name,
                    last_name=last_name,
                )
                # Link user to member (will be saved with member.save() at the end)
                member.user = user
            except Exception as e:
                # If user was created but linking fails, clean up the orphaned user
                if 'user' in locals() and user and user.id:
                    try:
                        user.delete()
                    except:
                        pass
                return JsonResponse({'success': False, 'error': f'Failed to create user account: {str(e)}'}, status=400)
    
    # Save all member changes (including user relationship if it was just set)
    member.save()

    return JsonResponse({
        'success': True,
        'message': 'Member updated successfully',
        'member': {
            'id': member.id,
            'name': member.full_name,
            'rfid': member.rfid_card_number,
            'email': member.email or '',
            'phone': member.phone,
            'member_type': member.member_type.name if member.member_type else '',
            'role': member.role,
            'is_active': member.is_active,
            'balance': str(member.balance),
        }
    })


@login_required
def member_management(request):
    # Allow admin and staff role users to access member management
    is_admin = is_admin_user(request.user)
    is_staff_role_user = is_staff_role(request.user)
    
    if not (is_admin or is_staff_role_user):
        messages.warning(request, 'You do not have permission to access this page.')
        return redirect('kiosk_home')
    
    # Get search query from request
    search_query = request.GET.get('search', '').strip()
    
    # Start with all active members only (exclude inactive)
    members = Member.objects.filter(is_active=True)
    
    # Apply search filter if query exists
    if search_query:
        # Build base query for all non-name fields
        search_filters = Q(
            Q(rfid_card_number__icontains=search_query) |
            Q(email__icontains=search_query) |
            Q(phone__icontains=search_query) |
            Q(member_type__name__icontains=search_query) |
            Q(role__icontains=search_query)
        )
        
        # Handle name search - check if query contains spaces (full name search)
        if ' ' in search_query:
            # Split the query into parts (handle multiple spaces)
            name_parts = [part.strip() for part in search_query.split() if part.strip()]
            
            if len(name_parts) >= 2:
                # Full name search: "John Doe" or "John Michael Doe"
                first_part = name_parts[0]
                remaining_parts = ' '.join(name_parts[1:])  # Join remaining parts for last name
                
                # Match combinations:
                # 1. First part in first_name AND remaining in last_name
                # 2. First part in last_name AND remaining in first_name (reverse)
                # 3. Full query in first_name or last_name (for partial matches)
                name_filter = (
                    (Q(first_name__icontains=first_part) & Q(last_name__icontains=remaining_parts)) |
                    (Q(first_name__icontains=remaining_parts) & Q(last_name__icontains=first_part)) |
                    Q(first_name__icontains=search_query) |
                    Q(last_name__icontains=search_query)
                )
                search_filters |= name_filter
            else:
                # Single word, search in both name fields
                search_filters |= Q(first_name__icontains=name_parts[0]) | Q(last_name__icontains=name_parts[0])
        else:
            # No spaces - single word search in first_name, last_name, or full name
            # Search individual fields
            search_filters |= Q(first_name__icontains=search_query) | Q(last_name__icontains=search_query)
            
            # Also try to match full name by checking if query matches start of first_name + last_name
            # This handles cases where user types "JohnDoe" (no space)
            # We'll search for members where first_name starts with query or last_name starts with query
            # This is already covered by the icontains above, but we can be more specific
        
        members = members.filter(search_filters)
    
    # Order by date joined
    members = members.order_by('-date_joined')
    member_types = MemberType.objects.all()
    
    # Calculate statistics (from all members, not filtered)
    all_members = Member.objects.all()
    total_members = all_members.count()
    active_members = all_members.filter(is_active=True).count()
    total_balances = all_members.aggregate(Sum('balance'))['balance__sum'] or 0
    
    # Check if current user is staff (to restrict role options)
    # Check the Member's role directly - if role is 'staff', restrict options
    user_is_staff = False
    try:
        member = Member.objects.get(user=request.user)
        if member.role == 'staff' and member.is_active:
            user_is_staff = True
    except Member.DoesNotExist:
        pass
    
    context = {
        'members': members,
        'member_types': member_types,
        'total_members': total_members,
        'active_members': active_members,
        'total_balances': total_balances,
        'is_staff': user_is_staff,
        'search_query': search_query,
    }
    
    return render(request, 'admin_panel/members.html', context)


@login_required
def backup_members_data(request):
    """Export all member data as CSV backup file."""
    # Check if user has permission (admin or staff role)
    is_admin = is_admin_user(request.user)
    is_staff_role_user = is_staff_role(request.user)
    
    if not (is_admin or is_staff_role_user):
        messages.warning(request, 'You do not have permission to access this page.')
        return redirect('kiosk_home')
    
    # Get backup date from request, default to today
    backup_date_str = request.GET.get('date', '')
    if backup_date_str:
        try:
            backup_date = datetime.strptime(backup_date_str, '%Y-%m-%d').date()
        except ValueError:
            backup_date = timezone.now().date()
    else:
        backup_date = timezone.now().date()
    
    # Convert backup_date to datetime for comparison (end of day)
    backup_datetime_end = timezone.make_aware(datetime.combine(backup_date, datetime.max.time()))
    
    # Get all members that existed on or before the backup date
    # This includes all members (active and inactive/deleted) created on or before the backup date
    members = Member.objects.select_related('member_type', 'user').filter(
        created_at__lte=backup_datetime_end
    ).order_by('id')
    
    # Create CSV response with date in filename
    date_str = backup_date.strftime('%Y%m%d')
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="members_backup_{date_str}.csv"'
    
    # Create CSV writer
    writer = csv.writer(response)
    
    # Write header row
    writer.writerow([
        'ID',
        'RFID Card Number',
        'First Name',
        'Last Name',
        'Full Name',
        'Email',
        'Phone',
        'Member Type',
        'Role',
        'Balance',
        'Is Active',
        'Username',
        'Has PIN Set',
        'Date Joined',
        'Last Transaction',
        'Created At',
        'Updated At'
    ])
    
    # Write member data
    for member in members:
        writer.writerow([
            member.id,
            member.rfid_card_number,
            member.first_name,
            member.last_name,
            member.full_name,
            member.email or '',
            member.phone or '',
            member.member_type.name if member.member_type else '',
            member.get_role_display(),
            str(member.balance),
            'Yes' if member.is_active else 'No',
            member.user.username if member.user else '',
            'Yes' if member.pin_hash else 'No',
            member.date_joined.strftime('%Y-%m-%d %H:%M:%S') if member.date_joined else '',
            member.last_transaction.strftime('%Y-%m-%d %H:%M:%S') if member.last_transaction else '',
            member.created_at.strftime('%Y-%m-%d %H:%M:%S') if member.created_at else '',
            member.updated_at.strftime('%Y-%m-%d %H:%M:%S') if member.updated_at else '',
        ])
    
    return response


@login_required
@require_http_methods(["POST"])
def restore_members_data(request):
    """Restore deleted/inactive members from a backup date by reactivating them."""
    # Check if user has permission (admin only for restore)
    if not is_admin_user(request.user):
        return JsonResponse({'success': False, 'error': 'Permission denied. Only admins can restore members.'}, status=403)
    
    try:
        data = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON payload'}, status=400)
    
    restore_date_str = data.get('date', '').strip()
    restore_all = data.get('restore_all', False)  # Optional flag to restore all inactive members
    
    # Initialize variables
    inactive_members = Member.objects.none()
    deleted_members_log = DeletedMember.objects.none()
    
    # If restore_all is True, restore all inactive members regardless of date
    if restore_all:
        # Restore ALL inactive members - no date filtering
        inactive_members = Member.objects.filter(is_active=False).order_by('id')
        deleted_members_log = DeletedMember.objects.filter(restored=False).order_by('deleted_at')
        restore_date_str = 'ALL'  # For display purposes
    elif restore_date_str:
        try:
            restore_date = datetime.strptime(restore_date_str, '%Y-%m-%d').date()
        except ValueError:
            return JsonResponse({'success': False, 'error': 'Invalid date format. Use YYYY-MM-DD'}, status=400)
        
        # Convert restore_date to datetime for comparison (end of day)
        restore_datetime_end = timezone.make_aware(datetime.combine(restore_date, datetime.max.time()))
        
        # Find inactive members that were created on or before the restore date
        inactive_members = Member.objects.filter(
            is_active=False,
            created_at__lte=restore_datetime_end
        ).order_by('id')
        
        # Also find deleted members from log that were deleted on or before restore date
        deleted_members_log = DeletedMember.objects.filter(
            restored=False,
            deleted_at__lte=restore_datetime_end
        ).order_by('deleted_at')
    else:
        return JsonResponse({'success': False, 'error': 'Backup date is required (or set restore_all=true)'}, status=400)
    
    # Get total counts for debugging
    all_inactive_count = Member.objects.filter(is_active=False).count()
    all_deleted_log_count = DeletedMember.objects.filter(restored=False).count()
    matching_inactive_count = inactive_members.count()
    matching_deleted_log_count = deleted_members_log.count()
    
    # Print restore operation header to terminal
    print("\n" + "="*80)
    if restore_all:
        print(f"RESTORE MEMBERS OPERATION - Restoring ALL inactive members and from deletion log")
    else:
        print(f"RESTORE MEMBERS OPERATION - Backup Date: {restore_date_str}")
    print(f"Requested by: {request.user.username} ({request.user.get_full_name() or 'N/A'})")
    print(f"Timestamp: {timezone.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("-"*80)
    print(f"DEBUG: Total inactive members in database: {all_inactive_count}")
    print(f"DEBUG: Inactive members matching restore criteria: {matching_inactive_count}")
    print(f"DEBUG: Total deleted members in log: {all_deleted_log_count}")
    print(f"DEBUG: Deleted members from log matching restore criteria: {matching_deleted_log_count}")
    print("="*80)
    
    restored_count = 0
    restored_members = []
    
    # Step 1: Restore inactive members (soft-deleted)
    inactive_members_list = list(inactive_members)
    print(f"\n[Step 1] Processing {len(inactive_members_list)} inactive member(s)...")
    
    for member in inactive_members_list:
        try:
            if not member.is_active:
                member.is_active = True
                member.save(update_fields=['is_active'])
                restored_count += 1
                
                member_info = {
                    'id': member.id,
                    'name': member.full_name,
                    'rfid': member.rfid_card_number,
                    'email': member.email or '',
                    'source': 'inactive'
                }
                restored_members.append(member_info)
                
                print(f"  [{restored_count}] ID: {member.id:4d} | Name: {member.full_name:30s} | "
                      f"RFID: {member.rfid_card_number:15s} | Email: {member.email or 'N/A'} | Source: Inactive")
        except Exception as e:
            print(f"  ERROR: Failed to restore member ID {member.id}: {str(e)}")
            continue
    
    # Step 2: Restore from deletion log
    deleted_members_list = list(deleted_members_log)
    print(f"\n[Step 2] Processing {len(deleted_members_list)} deleted member(s) from log...")
    
    for deleted_member in deleted_members_list:
        try:
            # Check if member already exists
            if Member.objects.filter(rfid_card_number=deleted_member.rfid_card_number).exists():
                print(f"  SKIP: Member with RFID {deleted_member.rfid_card_number} already exists, skipping...")
                continue
            
            if deleted_member.email and Member.objects.filter(email=deleted_member.email).exists():
                print(f"  SKIP: Member with email {deleted_member.email} already exists, skipping...")
                continue
            
            # Get member type if it exists
            member_type = None
            if deleted_member.member_type_name:
                try:
                    member_type = MemberType.objects.get(name=deleted_member.member_type_name)
                except MemberType.DoesNotExist:
                    pass
            
            # Get user if username was provided
            user = None
            if deleted_member.username:
                try:
                    user = User.objects.get(username=deleted_member.username)
                except User.DoesNotExist:
                    pass
            
            # Restore member
            restored_member = Member.objects.create(
                rfid_card_number=deleted_member.rfid_card_number,
                first_name=deleted_member.first_name,
                last_name=deleted_member.last_name,
                email=deleted_member.email,
                phone=deleted_member.phone,
                member_type=member_type,
                role=deleted_member.role,
                balance=deleted_member.balance,
                user=user,
                pin_hash=deleted_member.pin_hash,
                is_active=True,
                date_joined=deleted_member.original_date_joined or timezone.now(),
                last_transaction=deleted_member.original_last_transaction,
                created_at=deleted_member.original_created_at or timezone.now(),
                updated_at=timezone.now(),
            )
            
            # Mark as restored in log
            deleted_member.restored = True
            deleted_member.restored_at = timezone.now()
            deleted_member.restored_by = request.user.username
            deleted_member.save()
            
            restored_count += 1
            member_info = {
                'id': restored_member.id,
                'name': restored_member.full_name,
                'rfid': restored_member.rfid_card_number,
                'email': restored_member.email or '',
                'source': 'deletion_log'
            }
            restored_members.append(member_info)
            
            print(f"  [{restored_count}] ID: {restored_member.id:4d} | Name: {restored_member.full_name:30s} | "
                  f"RFID: {restored_member.rfid_card_number:15s} | Email: {restored_member.email or 'N/A'} | Source: Deletion Log")
        except Exception as e:
            print(f"  ERROR: Failed to restore deleted member {deleted_member.first_name} {deleted_member.last_name}: {str(e)}")
            continue
    
    if restored_count == 0:
        print("\n  No members found to restore for the selected criteria.")
        if all_inactive_count > 0 or all_deleted_log_count > 0:
            print(f"  NOTE: There are {all_inactive_count} inactive member(s) and {all_deleted_log_count} deleted member(s) in log,")
            print(f"        but they don't match the restore date criteria ({restore_date_str}).")
            print(f"        Try using 'Restore all' option or a more recent date.")
        print("="*80 + "\n")
        return JsonResponse({
            'success': True,
            'message': f'No inactive members found to restore for the date {restore_date_str}',
            'restored_count': 0,
            'restored_members': []
        })
    
    # Print summary to terminal
    print("-"*80)
    print(f"SUMMARY: Successfully restored {restored_count} member(s)")
    print("="*80 + "\n")
    
    return JsonResponse({
        'success': True,
        'message': f'Successfully restored {restored_count} member(s) from backup date {restore_date_str}',
        'restored_count': restored_count,
        'restored_members': restored_members
    })


@login_required
def transaction_history(request):
    if not is_admin_user(request.user):
        messages.warning(request, 'You do not have permission to access this page.')
        return redirect('kiosk_home')
    
    # Get all transactions with related data
    transactions_qs = Transaction.objects.select_related('member').prefetch_related('items').order_by('-created_at')
    paginator = Paginator(transactions_qs, 10)
    page_number = request.GET.get('page', 1)
    transactions_page = paginator.get_page(page_number)
    
    # Calculate statistics
    total_transactions = transactions_qs.count()
    completed_transactions = transactions_qs.filter(status='completed').count()
    pending_transactions = transactions_qs.filter(status='pending').count()
    cancelled_transactions = transactions_qs.filter(status='cancelled').count()
    total_revenue = transactions_qs.filter(status='completed').aggregate(Sum('total_amount'))['total_amount__sum'] or 0
    
    context = {
        'transactions': transactions_page,
        'page_obj': transactions_page,
        'total_transactions': total_transactions,
        'completed_transactions': completed_transactions,
        'pending_transactions': pending_transactions,
        'cancelled_transactions': cancelled_transactions,
        'total_revenue': total_revenue,
    }
    
    return render(request, 'admin_panel/transactions.html', context)


@require_http_methods(["GET", "POST"])
def admin_logout(request):
    """Custom admin logout that redirects to root login page (login.html)
    All users (including staff) are redirected to the login page after logout.
    """
    # Logout the user
    logout(request)
    messages.success(request, 'You have been successfully logged out.')
    
    # Redirect all users (including staff) to root login page without any query parameters
    # Use reverse to ensure we go to the root login page, not /admin/login/
    return redirect(reverse('root_login'))


@require_http_methods(["GET", "POST"])
def kiosk_logout(request):
    """Logout endpoint that renders login.html directly"""
    # Clear Django user session
    logout(request)
    
    # Clear member session data (for members without Django user accounts)
    if 'member_id' in request.session:
        del request.session['member_id']
    if 'member_rfid' in request.session:
        del request.session['member_rfid']
    if 'member_role' in request.session:
        del request.session['member_role']
    
    messages.success(request, 'You have been successfully logged out.')
    return render(request, 'admin_panel/login.html')


@member_or_login_required
def user_choice(request):
    """Choice page for regular users after login - view transactions or go to kiosk"""
    # Check if user is authenticated (Django user)
    if request.user.is_authenticated:
        if is_admin_user(request.user):
            return redirect('dashboard')
        # Get member associated with user for template
        member = None
        try:
            member = Member.objects.get(user=request.user, is_active=True)
        except (Member.DoesNotExist, Member.MultipleObjectsReturned):
            pass
        
        context = {
            'user': request.user,
            'member': member,
        }
        return render(request, 'admin_panel/user_choice.html', context)
    
    # Member without Django user account (session-based)
    member_id = request.session.get('member_id')
    if member_id:
        try:
            member = Member.objects.get(id=member_id, is_active=True)
            # Create a mock user object for template compatibility
            class MockUser:
                def __init__(self, member):
                    self.username = member.rfid_card_number
                    self.first_name = member.first_name
                    self.last_name = member.last_name
                    self.is_authenticated = True
                
                def get_full_name(self):
                    return f"{self.first_name} {self.last_name}".strip()
            
            mock_user = MockUser(member)
            context = {
                'user': mock_user,
                'member': member,
            }
            return render(request, 'admin_panel/user_choice.html', context)
        except Member.DoesNotExist:
            pass
    
    # Should not reach here due to decorator, but just in case
    messages.warning(request, 'Please log in to access this page.')
    return redirect('root_login')


@member_or_login_required
def user_transactions(request):
    """View last 10 transactions for the logged-in user or member"""
    # Check if user is authenticated (Django user)
    if request.user.is_authenticated:
        if is_admin_user(request.user):
            return redirect('dashboard')
        
        # Get member associated with user
        member = None
        transactions = []
        
        try:
            # Try to get the member associated with the logged-in user
            member = Member.objects.get(user=request.user, is_active=True)
            
            # Get last 10 completed transactions for this member
            # Prefetch related items to avoid N+1 queries
            transactions = Transaction.objects.filter(
                member=member,
                status='completed'
            ).select_related('member').prefetch_related('items').order_by('-created_at')[:10]
            
            # Force queryset evaluation by converting to list
            transactions = list(transactions)
            
            # Debug: If no transactions found, check if there are any transactions at all for this member
            if not transactions:
                # Check if there are any transactions (even with different status)
                all_transactions = Transaction.objects.filter(member=member).count()
                if all_transactions > 0:
                    # Check what statuses exist
                    statuses = Transaction.objects.filter(member=member).values_list('status', flat=True).distinct()
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.warning(f"Member {member.id} has {all_transactions} transactions but none with status 'completed'. Statuses found: {list(statuses)}")
        except Member.DoesNotExist:
            # User doesn't have a member account
            pass
        except Member.MultipleObjectsReturned:
            # Multiple members found for this user (shouldn't happen, but handle it)
            member = Member.objects.filter(user=request.user, is_active=True).first()
            if member:
                transactions = Transaction.objects.filter(
                    member=member,
                    status='completed'
                ).select_related('member').prefetch_related('items').order_by('-created_at')[:10]
                transactions = list(transactions)
        except Exception as e:
            # Log error for debugging
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error fetching transactions: {str(e)}", exc_info=True)
        
        context = {
            'transactions': transactions,
            'member': member,
            'user': request.user,
        }
        return render(request, 'admin_panel/user_transactions.html', context)
    
    # Member without Django user account (session-based)
    member_id = request.session.get('member_id')
    if member_id:
        try:
            member = Member.objects.get(id=member_id, is_active=True)
            
            # Get last 10 completed transactions for this member
            transactions = Transaction.objects.filter(
                member=member,
                status='completed'
            ).select_related('member').prefetch_related('items').order_by('-created_at')[:10]
            transactions = list(transactions)
            
            # Create a mock user object for template compatibility
            class MockUser:
                def __init__(self, member):
                    self.username = member.rfid_card_number
                    self.first_name = member.first_name
                    self.last_name = member.last_name
                    self.is_authenticated = True
                
                def get_full_name(self):
                    return f"{self.first_name} {self.last_name}".strip()
            
            mock_user = MockUser(member)
            
            context = {
                'transactions': transactions,
                'member': member,
                'user': mock_user,
            }
            return render(request, 'admin_panel/user_transactions.html', context)
        except Member.DoesNotExist:
            pass
    
    # Should not reach here due to decorator, but just in case
    messages.warning(request, 'Please log in to access this page.')
    return redirect('root_login')


@require_http_methods(["POST"])
def api_rfid_login(request):
    """Login directly using RFID card - authenticates and logs in the user.
    For members with role "member" without username, stores member info in session."""
    try:
        data = json.loads(request.body)
        rfid = data.get('rfid')
        
        if not rfid:
            return JsonResponse({'success': False, 'error': 'RFID is required'})
        
        try:
            member = Member.objects.get(rfid_card_number=rfid, is_active=True)
        except Member.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Member not found or inactive'})
        
        # For members with role "member" without username, allow login using session
        if member.role == 'member' and (member.user is None or not member.user.username or not member.user.is_active):
            # Store member info in session for members without username
            request.session['member_id'] = member.id
            request.session['member_rfid'] = member.rfid_card_number
            request.session['member_role'] = member.role
            
            # Determine redirect URL
            next_url = data.get('next') or 'user_choice'
            if next_url and next_url.startswith('/') and next_url != '/admin/':
                redirect_url = next_url
            else:
                redirect_url = '/user-choice/'
            
            return JsonResponse({
                'success': True,
                'message': f'Welcome back, {member.full_name}!',
                'redirect_url': redirect_url,
                'member_only': True,
                'member': {
                    'id': member.id,
                    'name': member.full_name,
                    'rfid': member.rfid_card_number,
                }
            })
        
        # For members with user account, require active user
        if not member.user or not member.user.is_active:
            return JsonResponse({'success': False, 'error': 'No active user account linked to this RFID card'})
        
        # Log in the user
        login(request, member.user)
        
        # Determine redirect URL
        next_url = data.get('next') or 'dashboard'
        if is_admin_user(member.user):
            if next_url == 'dashboard':
                redirect_url = '/dashboard/'
            elif next_url and next_url.startswith('/') and next_url != '/admin/':
                redirect_url = next_url
            else:
                redirect_url = '/dashboard/'
        else:
            # Regular users go to choice page
            if next_url and next_url.startswith('/') and next_url != '/admin/':
                redirect_url = next_url
            else:
                redirect_url = '/user-choice/'
        
        return JsonResponse({
            'success': True,
            'message': f'Welcome back, {member.user.get_full_name() or member.user.username}!',
            'redirect_url': redirect_url,
            'user': {
                'username': member.user.username,
                'name': member.user.get_full_name() or member.user.username,
            }
        })
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON data'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Server error: {str(e)}'})


@login_required
@require_http_methods(["GET"])
def api_search_members(request):
    """Search members by RFID card number or name for balance refill - accessible to admin and staff"""
    # Allow admin and staff role users to search members
    is_admin = is_admin_user(request.user)
    is_staff_role_user = is_staff_role(request.user)
    
    if not (is_admin or is_staff_role_user):
        return JsonResponse({'success': False, 'error': 'Permission denied'}, status=403)
    query = request.GET.get('q', '').strip()
    
    if not query or len(query) < 2:
        return JsonResponse({'success': True, 'members': []})
    
    try:
        # Search by RFID (exact or partial) or by name
        members = Member.objects.filter(
            Q(rfid_card_number__icontains=query) |
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(email__icontains=query)
        ).filter(is_active=True)[:20]
        
        results = []
        for member in members:
            results.append({
                'id': member.id,
                'rfid': member.rfid_card_number,
                'name': member.full_name,
                'email': member.email or '',
                'current_balance': str(member.balance),
            })
        
        return JsonResponse({'success': True, 'members': results})
    except Exception as e:
        return JsonResponse({'success': False, 'error': 'Server error occurred'})


@login_required
@require_http_methods(["POST"])
def api_refill_balance(request):
    """Refill/add balance to a member's card - accessible to admin and staff role users"""
    # Check if user is admin or staff (staff role from Member model)
    is_admin = is_admin_user(request.user)
    is_staff_role_user = is_staff_role(request.user)
    
    if not (is_admin or is_staff_role_user):
        return JsonResponse({'success': False, 'error': 'Permission denied. Only admin and staff can refill balances.'}, status=403)
    
    try:
        data = json.loads(request.body)
        member_id = data.get('member_id')
        amount = data.get('amount')
        notes = data.get('notes', '').strip()
        
        if not member_id:
            return JsonResponse({'success': False, 'error': 'Member ID is required'})
        
        if not amount:
            return JsonResponse({'success': False, 'error': 'Amount is required'})
        
        try:
            amount = Decimal(str(amount))
            if amount <= 0:
                return JsonResponse({'success': False, 'error': 'Amount must be greater than zero'})
        except (InvalidOperation, ValueError):
            return JsonResponse({'success': False, 'error': 'Invalid amount format'})
        
        try:
            member = Member.objects.get(id=member_id, is_active=True)
        except Member.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Member not found'})
        
        # Get the staff member who is performing the refill (if staff role)
        staff_member = None
        performed_by_role = 'admin'
        performed_by_name = request.user.get_full_name() or request.user.username
        
        if is_staff_role_user and not is_admin:
            try:
                staff_member = Member.objects.get(user=request.user, is_active=True)
                if staff_member.role == 'staff':
                    performed_by_role = 'staff'
                    performed_by_name = staff_member.full_name
            except Member.DoesNotExist:
                pass
        
        # Record balance before
        balance_before = member.balance
        
        # Add balance
        member.add_balance(amount)
        
        # Record balance after
        balance_after = member.balance
        
        # Create balance transaction record with performer information
        transaction_notes = f"Balance refill by {performed_by_role}"
        if notes:
            transaction_notes += f". {notes}"
        
        BalanceTransaction.objects.create(
            member=member,
            transaction_type='deposit',
            amount=amount,
            balance_before=balance_before,
            balance_after=balance_after,
            notes=transaction_notes
        )
        
        # Send email to admin if staff member performed the refill
        if performed_by_role == 'staff' and staff_member:
            try:
                admin_email = get_admin_email()
                if admin_email:
                    # Create email subject and body
                    subject = f'Balance Refill Transaction - {member.full_name}'
                    email_body = f"""
A balance refill transaction has been performed by a staff member.

Transaction Details:
- Member: {member.full_name} ({member.rfid_card_number})
- Amount Added: ₱{amount:.2f}
- Balance Before: ₱{balance_before:.2f}
- Balance After: ₱{balance_after:.2f}

Performed By:
- Name: {performed_by_name}
- Role: Staff
- User: {request.user.username}

Additional Notes: {notes if notes else 'None'}

Timestamp: {timezone.now().strftime('%Y-%m-%d %H:%M:%S')}
                    """
                    
                    # Send email
                    email = EmailMessage(
                        subject=subject,
                        body=email_body.strip(),
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        to=[admin_email],
                    )
                    email.send(fail_silently=False)
            except Exception as email_error:
                # Log error but don't fail the transaction
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f'Failed to send balance refill email notification: {str(email_error)}', exc_info=True)
        
        return JsonResponse({
            'success': True,
            'message': f'Successfully added ₱{amount:.2f} to {member.full_name}\'s balance',
            'member': {
                'id': member.id,
                'name': member.full_name,
                'rfid': member.rfid_card_number,
                'new_balance': str(member.balance),
            }
        })
        
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON data'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Server error: {str(e)}'})


def generate_refund_receipt_data(transaction, refund_reason, member, balance_before=None, balance_after=None, request=None):
    """Generate refund receipt text data"""
    from django.conf import settings
    
    vat_rate = getattr(settings, 'VAT_RATE', 0.12)
    lines = []
    
    def money(v):
        if v is None:
            return '₱0.00'
        return '₱' + str(Decimal(str(v)).quantize(Decimal('0.01')))
    
    # Header
    lines.append('GENGLO PRINTING SERVICES')
    lines.append('REFUND RECEIPT')
    lines.append('')
    
    # Transaction info
    lines.append('Original Txn:')
    lines.append(transaction.transaction_number)
    lines.append('Refund Date:')
    lines.append(timezone.localtime(timezone.now()).strftime('%Y-%m-%d %H:%M:%S'))
    lines.append('')
    
    # Member info
    if member:
        lines.append('Member:')
        lines.append(member.full_name)
        if hasattr(member, 'member_id') and member.member_id:
            lines.append(f'Member ID: {member.member_id}')
        lines.append('')
    
    # Items refunded
    lines.append('ITEMS REFUNDED:')
    for item in transaction.items.all():
        name = item.product_name
        qty = item.quantity
        total = money(item.total_price)
        lines.append(f'{name} x{qty}')
        lines.append(total)
    lines.append('')
    
    # Amounts
    lines.append('Vatable Sale:')
    lines.append(money(transaction.vatable_sale))
    lines.append(f'VAT ({vat_rate*100:.0f}%):')
    lines.append(money(transaction.vat_amount))
    lines.append('Subtotal:')
    lines.append(money(transaction.subtotal))
    lines.append('Total Refund:')
    lines.append(money(transaction.total_amount))
    lines.append('')
    
    # Payment method refund info - All refunds now go to balance
    lines.append('REFUND METHOD:')
    if member and balance_before is not None:
        lines.append('Refunded to Member Balance')
        lines.append(f'Balance Before: {money(balance_before)}')
        lines.append(f'Balance After: {money(balance_after)}')
    else:
        lines.append('Refunded to Member Balance')
    lines.append('')
    
    # Reason if provided
    if refund_reason:
        lines.append('Reason:')
        lines.append(refund_reason)
        lines.append('')
    
    lines.append('Thank you!')
    
    return {
        'text': '\r\n'.join(lines),
        'html': generate_refund_receipt_html(transaction, refund_reason, member, balance_before, balance_after, request=request)
    }


def generate_refund_receipt_html(transaction, refund_reason, member, balance_before=None, balance_after=None, request=None):
    """Generate HTML version of refund receipt using template"""
    from django.conf import settings
    
    vat_rate = getattr(settings, 'VAT_RATE', 0.12)
    vat_rate_percent = int(vat_rate * 100)
    
    # Get shop information
    shop_name = getattr(settings, 'SHOP_NAME', 'GENGLO PRINTING SERVICES')
    shop_address = getattr(settings, 'SHOP_ADDRESS', 'Address: Lorem Ipsum, 23-10')
    shop_phone = getattr(settings, 'SHOP_PHONE', 'Telp. 11223344')
    
    # Determine refund method display - All refunds now go to balance
    show_balance_refund = (member and balance_before is not None)
    show_cash_refund = False  # Cash refunds also go to balance now
    
    context = {
        'transaction': transaction,
        'member': member,
        'refund_reason': refund_reason,
        'refund_date': timezone.localtime(timezone.now()),
        'vat_rate_percent': vat_rate_percent,
        'balance_before': balance_before,
        'balance_after': balance_after,
        'show_balance_refund': show_balance_refund,
        'show_cash_refund': show_cash_refund,
        'shop_name': shop_name,
        'shop_address': shop_address,
        'shop_phone': shop_phone,
    }
    
    # Render the template - use request if provided for proper context
    if request:
        html = render_to_string('admin_panel/refund_receipt.html', context, request=request)
    else:
        html = render_to_string('admin_panel/refund_receipt.html', context)
    
    return html


@login_required
def process_refund(request):
    """Refund management page - accessible to all logged-in users
    
    Access control:
    - Regular members: can only search and refund their own transactions
    - Cashiers and admins: can search and refund all transactions
    """
    # Check if user is cashier or admin
    has_full_access = is_cashier_or_admin(request.user)
    
    # Get today's date range in local timezone
    today = timezone.localtime(timezone.now()).date()
    today_start = timezone.make_aware(datetime.combine(today, datetime.min.time()))
    today_end = timezone.make_aware(datetime.combine(today, datetime.max.time()))
    
    # Base query for today's completed transactions
    today_transactions = Transaction.objects.filter(
        created_at__range=[today_start, today_end],
        status='completed'
    ).select_related('member').prefetch_related('items').order_by('-created_at')[:10]
    
    # If user is not cashier/admin, filter to only their own transactions
    if not has_full_access:
        try:
            member = Member.objects.get(user=request.user, is_active=True)
            today_transactions = today_transactions.filter(member=member)
        except Member.DoesNotExist:
            today_transactions = Transaction.objects.none()
        except Member.MultipleObjectsReturned:
            member = Member.objects.filter(user=request.user, is_active=True).first()
            if member:
                today_transactions = today_transactions.filter(member=member)
            else:
                today_transactions = Transaction.objects.none()
    
    # Prepare transaction data for template
    transactions_data = []
    for transaction in today_transactions:
        transactions_data.append({
            'id': transaction.id,
            'transaction_number': transaction.transaction_number,
            'member_name': transaction.member.full_name if transaction.member else 'Guest',
            'total_amount': transaction.total_amount,
            'payment_method': transaction.get_payment_method_display(),
            'created_at': timezone.localtime(transaction.created_at).strftime('%Y-%m-%d %H:%M:%S'),
            'items_count': transaction.items.count(),
        })
    
    context = {
        'today_transactions': transactions_data,
    }
    
    return render(request, 'admin_panel/refund.html', context)


@login_required
@require_http_methods(["GET"])
def view_refund_receipt(request, transaction_id):
    """View refund receipt for a cancelled transaction
    
    Access control:
    - Regular members: can only view receipts for their own transactions
    - Cashiers and admins: can view any transaction receipt
    """
    try:
        # Get the transaction - must be cancelled (refunded)
        transaction = Transaction.objects.select_related('member').prefetch_related('items').get(
            id=transaction_id, 
            status='cancelled'
        )
        
        # Check access control
        has_full_access = is_cashier_or_admin(request.user)
        if not has_full_access:
            # Get member associated with the logged-in user
            try:
                user_member = Member.objects.get(user=request.user, is_active=True)
            except Member.DoesNotExist:
                messages.error(request, 'You do not have permission to view this receipt')
                return redirect('process_refund')
            except Member.MultipleObjectsReturned:
                user_member = Member.objects.filter(user=request.user, is_active=True).first()
                if not user_member:
                    messages.error(request, 'You do not have permission to view this receipt')
                    return redirect('process_refund')
            
            # Check if the transaction belongs to the user
            if transaction.member != user_member:
                messages.error(request, 'You can only view receipts for your own transactions')
                return redirect('process_refund')
        
        member = transaction.member
        
        # Extract refund reason from transaction notes
        refund_reason = ''
        if transaction.notes and 'Refunded' in transaction.notes:
            # Extract reason if it exists (format: "Refunded. reason text")
            parts = transaction.notes.split('.', 1)
            if len(parts) > 1:
                refund_reason = parts[1].strip()
        
        # Try to get balance information from BalanceTransaction
        balance_before = None
        balance_after = None
        # Look for the most recent balance transaction related to this refund
        balance_txn = BalanceTransaction.objects.filter(
            notes__icontains=f'transaction {transaction.transaction_number}'
        ).filter(
            notes__icontains='Refund'
        ).order_by('-created_at').first()
        
        if balance_txn:
            balance_before = balance_txn.balance_before
            balance_after = balance_txn.balance_after
        elif member:
            # For cash refunds or if balance transaction not found, show current balance
            # Balance doesn't change for cash refunds, so before = after = current balance
            if transaction.payment_method == 'cash':
                balance_before = member.balance
                balance_after = member.balance
            else:
                # For other cases, try to get current balance as fallback
                balance_after = member.balance
        
        # Prepare context for template
        from django.conf import settings
        vat_rate = getattr(settings, 'VAT_RATE', 0.12)
        vat_rate_percent = int(vat_rate * 100)
        
        # Get shop information
        shop_name = getattr(settings, 'SHOP_NAME', 'GENGLO PRINTING SERVICES')
        shop_address = getattr(settings, 'SHOP_ADDRESS', 'Address: Lorem Ipsum, 23-10')
        shop_phone = getattr(settings, 'SHOP_PHONE', 'Telp. 11223344')
        
        # All refunds now go to balance, regardless of original payment method
        show_balance_refund = (member and balance_before is not None)
        show_cash_refund = False  # Cash refunds also go to balance now        
        context = {
            'transaction': transaction,
            'member': member,
            'refund_reason': refund_reason,
            'refund_date': timezone.localtime(transaction.updated_at) if transaction.updated_at else timezone.localtime(timezone.now()),  # Use when transaction was cancelled, converted to local timezone
            'vat_rate_percent': vat_rate_percent,
            'balance_before': balance_before,
            'balance_after': balance_after,
            'show_balance_refund': show_balance_refund,
            'show_cash_refund': show_cash_refund,
            'shop_name': shop_name,
            'shop_address': shop_address,
            'shop_phone': shop_phone,
        }
        
        return render(request, 'admin_panel/refund_receipt.html', context)
        
    except Transaction.DoesNotExist:
        messages.error(request, 'Refund receipt not found')
        return redirect('process_refund')
    except Exception as e:
        messages.error(request, f'Error loading receipt: {str(e)}')
        return redirect('process_refund')


@login_required
@require_http_methods(["GET"])
def view_cash_receipt(request, transaction_id):
    """View cash receipt for a completed cash transaction
    
    Access control:
    - Regular members: can only view receipts for their own transactions
    - Cashiers and admins: can view any transaction receipt
    """
    try:
        # Get the transaction - must be completed and cash payment
        transaction = Transaction.objects.select_related('member').prefetch_related('items').get(
            id=transaction_id, 
            status='completed',
            payment_method='cash'
        )
        
        # Check access control
        has_full_access = is_cashier_or_admin(request.user)
        if not has_full_access:
            # Get member associated with the logged-in user
            try:
                user_member = Member.objects.get(user=request.user, is_active=True)
            except Member.DoesNotExist:
                messages.error(request, 'You do not have permission to view this receipt')
                return redirect('transaction_history')
            except Member.MultipleObjectsReturned:
                user_member = Member.objects.filter(user=request.user, is_active=True).first()
                if not user_member:
                    messages.error(request, 'You do not have permission to view this receipt')
                    return redirect('transaction_history')
            
            # Check if the transaction belongs to the user
            if transaction.member != user_member:
                messages.error(request, 'You can only view receipts for your own transactions')
                return redirect('transaction_history')
        
        # Calculate change amount
        change_amount = Decimal('0.00')
        if transaction.amount_paid > transaction.total_amount:
            change_amount = transaction.amount_paid - transaction.total_amount
        
        # Get shop information from settings (with defaults)
        shop_name = getattr(settings, 'SHOP_NAME', 'GENGLO PRINTING SERVICES')
        shop_address = getattr(settings, 'SHOP_ADDRESS', 'Address: Lorem Ipsum, 23-10')
        shop_phone = getattr(settings, 'SHOP_PHONE', 'Telp. 11223344')
        
        context = {
            'transaction': transaction,
            'change_amount': change_amount,
            'shop_name': shop_name,
            'shop_address': shop_address,
            'shop_phone': shop_phone,
        }
        
        return render(request, 'admin_panel/cash_receipt.html', context)
        
    except Transaction.DoesNotExist:
        messages.error(request, 'Cash receipt not found')
        return redirect('transaction_history')
    except Exception as e:
        messages.error(request, f'Error loading receipt: {str(e)}')
        return redirect('transaction_history')


@login_required
@require_http_methods(["GET"])
def view_debit_credit_receipt(request, transaction_id):
    """View debit receipt for a completed debit transaction
    
    Access control:
    - Regular members: can only view receipts for their own transactions
    - Cashiers and admins: can view any transaction receipt
    """
    try:
        # Get the transaction - must be completed and debit payment
        transaction = Transaction.objects.select_related('member').prefetch_related('items').get(
            id=transaction_id, 
            status='completed',
            payment_method='debit'
        )
        
        # Check access control
        has_full_access = is_cashier_or_admin(request.user)
        if not has_full_access:
            # Get member associated with the logged-in user
            try:
                user_member = Member.objects.get(user=request.user, is_active=True)
            except Member.DoesNotExist:
                messages.error(request, 'You do not have permission to view this receipt')
                return redirect('transaction_history')
            except Member.MultipleObjectsReturned:
                user_member = Member.objects.filter(user=request.user, is_active=True).first()
                if not user_member:
                    messages.error(request, 'You do not have permission to view this receipt')
                    return redirect('transaction_history')
            
            # Check if the transaction belongs to the user
            if transaction.member != user_member:
                messages.error(request, 'You can only view receipts for your own transactions')
                return redirect('transaction_history')
        
        # Get shop information from settings (with defaults)
        shop_name = getattr(settings, 'SHOP_NAME', 'BUSINESS NAME')
        shop_address = getattr(settings, 'SHOP_ADDRESS', '1234 Main Street, Suite 567, City Name, State 54321')
        shop_phone = getattr(settings, 'SHOP_PHONE', '123-456-7890')
        merchant_id = getattr(settings, 'MERCHANT_ID', None)
        terminal_id = getattr(settings, 'TERMINAL_ID', None)
        approval_code = getattr(settings, 'APPROVAL_CODE', None)
        
        # Refresh member to get latest balance for transparency
        if transaction.member:
            transaction.member.refresh_from_db()
        
        context = {
            'transaction': transaction,
            'shop_name': shop_name,
            'shop_address': shop_address,
            'shop_phone': shop_phone,
            'merchant_id': merchant_id,
            'terminal_id': terminal_id,
            'approval_code': approval_code,
        }
        
        return render(request, 'admin_panel/debit_credit_receipt.html', context)
        
    except Transaction.DoesNotExist:
        messages.error(request, 'Receipt not found')
        return redirect('transaction_history')
    except Exception as e:
        messages.error(request, f'Error loading receipt: {str(e)}')
        return redirect('transaction_history')


@login_required
@require_http_methods(["GET"])
def api_search_transactions_for_refund(request):
    """Search transactions by transaction number for refund processing
    
    Access control:
    - Regular members: can only search their own transactions
    - Cashiers and admins: can search all transactions
    """
    query = request.GET.get('q', '').strip()
    
    if not query or len(query) < 2:
        return JsonResponse({'success': True, 'transactions': []})
    
    try:
        # Check if user is cashier or admin
        has_full_access = is_cashier_or_admin(request.user)
        
        # Base query for completed transactions
        transactions = Transaction.objects.filter(
            transaction_number__icontains=query,
            status='completed'
        ).select_related('member').prefetch_related('items')
        
        # If user is not cashier/admin, filter to only their own transactions
        if not has_full_access:
            # Get member associated with the logged-in user
            try:
                member = Member.objects.get(user=request.user, is_active=True)
                transactions = transactions.filter(member=member)
            except Member.DoesNotExist:
                # User doesn't have a member account, return empty results
                return JsonResponse({'success': True, 'transactions': []})
            except Member.MultipleObjectsReturned:
                # Multiple members found, use the first one
                member = Member.objects.filter(user=request.user, is_active=True).first()
                if member:
                    transactions = transactions.filter(member=member)
                else:
                    return JsonResponse({'success': True, 'transactions': []})
        
        # Order and limit results
        transactions = transactions.order_by('-created_at')[:20]
        
        results = []
        for transaction in transactions:
            # Get transaction items
            items = []
            for item in transaction.items.all():
                items.append({
                    'product_name': item.product_name,
                    'quantity': item.quantity,
                    'total_price': str(item.total_price),
                })
            
            results.append({
                'id': transaction.id,
                'transaction_number': transaction.transaction_number,
                'member_name': transaction.member.full_name if transaction.member else 'Guest',
                'member_id': transaction.member.id if transaction.member else None,
                'total_amount': str(transaction.total_amount),
                'payment_method': transaction.get_payment_method_display(),
                'created_at': timezone.localtime(transaction.created_at).strftime('%Y-%m-%d %H:%M:%S'),
                'items_count': transaction.items.count(),
                'items': items,
            })
        
        return JsonResponse({'success': True, 'transactions': results})
    except Exception as e:
        return JsonResponse({'success': False, 'error': 'Server error occurred'})


@login_required
@require_http_methods(["GET"])
def api_search_transactions(request):
    """Search transactions with filters for admin management"""
    if not is_admin_user(request.user):
        return JsonResponse({'success': False, 'error': 'Permission denied'}, status=403)
    
    try:
        transaction_number = request.GET.get('transaction_number', '').strip()
        status = request.GET.get('status', '').strip()
        payment_method = request.GET.get('payment_method', '').strip()
        date_from = request.GET.get('date_from', '').strip()
        date_to = request.GET.get('date_to', '').strip()
        
        # Build query
        transactions_qs = Transaction.objects.select_related('member').prefetch_related('items').all()
        
        if transaction_number:
            transactions_qs = transactions_qs.filter(transaction_number__icontains=transaction_number)
        if status:
            transactions_qs = transactions_qs.filter(status=status)
        if payment_method:
            transactions_qs = transactions_qs.filter(payment_method=payment_method)
        if date_from:
            try:
                from_date = datetime.strptime(date_from, '%Y-%m-%d').date()
                transactions_qs = transactions_qs.filter(created_at__date__gte=from_date)
            except ValueError:
                pass
        if date_to:
            try:
                to_date = datetime.strptime(date_to, '%Y-%m-%d').date()
                transactions_qs = transactions_qs.filter(created_at__date__lte=to_date)
            except ValueError:
                pass
        
        # Order and limit
        transactions_qs = transactions_qs.order_by('-created_at')[:50]
        
        results = []
        for transaction in transactions_qs:
            local_created_at = timezone.localtime(transaction.created_at)
            results.append({
                'id': transaction.id,
                'transaction_number': transaction.transaction_number,
                'member_name': transaction.member.full_name if transaction.member else 'Guest',
                'member_rfid': transaction.member.rfid_card_number if transaction.member else None,
                'date': local_created_at.strftime('%Y-%m-%d'),
                'time': local_created_at.strftime('%H:%M:%S'),
                'amount': str(transaction.total_amount),
                'payment_method': transaction.payment_method,
                'payment_method_display': transaction.get_payment_method_display(),
                'status': transaction.status,
                'status_display': transaction.get_status_display(),
                'amount_paid': str(transaction.amount_paid),
                'amount_from_balance': str(transaction.amount_from_balance),
                'notes': transaction.notes or '',
            })
        
        return JsonResponse({'success': True, 'transactions': results})
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Server error: {str(e)}'})


@login_required
@require_http_methods(["GET"])
def api_get_transaction(request, transaction_id):
    """Get transaction details by ID"""
    if not is_admin_user(request.user):
        return JsonResponse({'success': False, 'error': 'Permission denied'}, status=403)
    
    try:
        transaction = Transaction.objects.select_related('member').prefetch_related('items').get(id=transaction_id)
        
        items = []
        for item in transaction.items.all():
            items.append({
                'product_name': item.product_name,
                'product_barcode': item.product_barcode,
                'quantity': item.quantity,
                'unit_price': str(item.unit_price),
                'total_price': str(item.total_price),
            })
        
        return JsonResponse({
            'success': True,
            'transaction': {
                'id': transaction.id,
                'transaction_number': transaction.transaction_number,
                'member_id': transaction.member.id if transaction.member else None,
                'member_name': transaction.member.full_name if transaction.member else 'Guest',
                'member_rfid': transaction.member.rfid_card_number if transaction.member else None,
                'subtotal': str(transaction.subtotal),
                'vatable_sale': str(transaction.vatable_sale),
                'vat_amount': str(transaction.vat_amount),
                'total_amount': str(transaction.total_amount),
                'payment_method': transaction.payment_method,
                'payment_method_display': transaction.get_payment_method_display(),
                'amount_paid': str(transaction.amount_paid),
                'amount_from_balance': str(transaction.amount_from_balance),
                'status': transaction.status,
                'status_display': transaction.get_status_display(),
                'notes': transaction.notes or '',
                'created_at': timezone.localtime(transaction.created_at).strftime('%Y-%m-%d %H:%M:%S'),
                'items': items,
            }
        })
    except Transaction.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Transaction not found'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Server error: {str(e)}'})


@login_required
@require_http_methods(["POST"])
def api_update_transaction(request):
    """Update a transaction without using the Django admin UI"""
    if not is_admin_user(request.user):
        return JsonResponse({'success': False, 'error': 'Permission denied'}, status=403)
    
    try:
        data = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON payload'}, status=400)
    
    transaction_id = data.get('transaction_id')
    if not transaction_id:
        return JsonResponse({'success': False, 'error': 'Transaction ID is required'}, status=400)
    
    try:
        transaction = Transaction.objects.get(id=transaction_id)
    except Transaction.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Transaction not found'}, status=404)
    
    # Update fields
    if 'status' in data:
        status = data.get('status', '').strip()
        if status in dict(Transaction.STATUS_CHOICES):
            transaction.status = status
    
    if 'payment_method' in data:
        payment_method = data.get('payment_method', '').strip()
        if payment_method in dict(Transaction.PAYMENT_METHODS):
            transaction.payment_method = payment_method
    
    if 'amount_paid' in data:
        try:
            amount_paid = Decimal(str(data.get('amount_paid', '0')))
            if amount_paid >= 0:
                transaction.amount_paid = amount_paid
        except (InvalidOperation, TypeError, ValueError):
            pass
    
    if 'amount_from_balance' in data:
        try:
            amount_from_balance = Decimal(str(data.get('amount_from_balance', '0')))
            if amount_from_balance >= 0:
                transaction.amount_from_balance = amount_from_balance
        except (InvalidOperation, TypeError, ValueError):
            pass
    
    if 'notes' in data:
        transaction.notes = (data.get('notes') or '').strip()
    
    transaction.save()
    
    return JsonResponse({
        'success': True,
        'message': 'Transaction updated successfully',
        'transaction': {
            'id': transaction.id,
            'transaction_number': transaction.transaction_number,
            'status': transaction.status,
            'status_display': transaction.get_status_display(),
            'payment_method': transaction.payment_method,
            'payment_method_display': transaction.get_payment_method_display(),
            'total_amount': str(transaction.total_amount),
        }
    })


@login_required
@require_http_methods(["POST"])
def api_process_refund(request):
    """Process a refund for a transaction
    
    Access control:
    - Regular members: can only refund their own transactions
    - Cashiers and admins: can refund any transaction
    """
    try:
        data = json.loads(request.body)
        transaction_id = data.get('transaction_id')
        refund_reason = data.get('reason', '').strip()
        
        if not transaction_id:
            return JsonResponse({'success': False, 'error': 'Transaction ID is required'})
        
        try:
            # Prefetch related items for receipt generation
            transaction = Transaction.objects.select_related('member').prefetch_related('items').get(id=transaction_id, status='completed')
        except Transaction.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Transaction not found or not eligible for refund'})
        
        # Check access control: regular members can only refund their own transactions
        has_full_access = is_cashier_or_admin(request.user)
        if not has_full_access:
            # Get member associated with the logged-in user
            try:
                user_member = Member.objects.get(user=request.user, is_active=True)
            except Member.DoesNotExist:
                return JsonResponse({'success': False, 'error': 'You do not have permission to process refunds'}, status=403)
            except Member.MultipleObjectsReturned:
                user_member = Member.objects.filter(user=request.user, is_active=True).first()
                if not user_member:
                    return JsonResponse({'success': False, 'error': 'You do not have permission to process refunds'}, status=403)
            
            # Check if the transaction belongs to the user
            if transaction.member != user_member:
                return JsonResponse({'success': False, 'error': 'You can only refund your own transactions'}, status=403)
        
        member = transaction.member
        
        # Capture balances before refund for receipt
        balance_before = None
        balance_after = None
        # Process refund - ALL refunds go directly to card balance regardless of payment method
        if member:
            # Refund to balance for all payment methods
            balance_before = member.balance
            member.add_balance(transaction.total_amount)
            balance_after = member.balance
            
            # Record balance transaction
            BalanceTransaction.objects.create(
                member=member,
                transaction_type='deposit',
                amount=transaction.total_amount,
                balance_before=balance_before,
                balance_after=balance_after,
                notes=f"Refund for transaction {transaction.transaction_number} (Original: {transaction.get_payment_method_display()}). {refund_reason}" if refund_reason else f"Refund for transaction {transaction.transaction_number} (Original: {transaction.get_payment_method_display()})"
            )
        
        # Restore product stock
        for item in transaction.items.all():
            if item.product:
                item.product.stock_quantity += item.quantity
                item.product.save()
        
        # Mark transaction as cancelled
        transaction.status = 'cancelled'
        transaction.notes = f"Refunded. {refund_reason}" if refund_reason else "Refunded"
        transaction.save()
        
        # Refresh member to get updated balances
        if member:
            member.refresh_from_db()
        
        # Generate refund receipt data - pass request for proper template rendering
        receipt_data = generate_refund_receipt_data(transaction, refund_reason, member, balance_before, balance_after, request=request)
        
        return JsonResponse({
            'success': True,
            'message': f'Successfully processed refund for transaction {transaction.transaction_number}',
            'transaction': {
                'id': transaction.id,
                'transaction_number': transaction.transaction_number,
                'refund_amount': str(transaction.total_amount),
            },
            'receipt': receipt_data
        })
        
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON data'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Server error: {str(e)}'})


@login_required
@require_http_methods(["GET"])
def generate_daily_report_pdf(request):
    """Generate and download a daily sales and stock report as PDF"""
    if not is_admin_user(request.user):
        messages.warning(request, 'You do not have permission to generate reports.')
        return redirect('dashboard')
    
    # Get date from query parameter, default to today
    date_str = request.GET.get('date', '')
    if date_str:
        try:
            report_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            messages.error(request, 'Invalid date format. Use YYYY-MM-DD')
            return redirect('dashboard')
    else:
        report_date = timezone.now().date()
    
    # Generate PDF
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, 
                           rightMargin=30, leftMargin=30,
                           topMargin=30, bottomMargin=18)
    
    # Container for the 'Flowable' objects
    elements = []
    styles = getSampleStyleSheet()
    
    # Use "PHP" instead of peso sign for better font compatibility in PDF
    currency_symbol = "PHP "
    
    # Define custom styles
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        textColor=colors.HexColor('#1a237e'),
        spaceAfter=30,
        alignment=TA_CENTER,
        fontName='Helvetica-Bold'
    )
    
    heading_style = ParagraphStyle(
        'CustomHeading',
        parent=styles['Heading2'],
        fontSize=14,
        textColor=colors.HexColor('#283593'),
        spaceAfter=12,
        spaceBefore=12,
        fontName='Helvetica-Bold'
    )
    
    # Title
    title = Paragraph("Daily Sales & Stock Report", title_style)
    elements.append(title)
    
    date_str = report_date.strftime('%B %d, %Y')
    date_para = Paragraph(f"Report Date: {date_str}", styles['Normal'])
    elements.append(date_para)
    elements.append(Spacer(1, 0.3*inch))
    
    # ===== SALES SUMMARY =====
    elements.append(Paragraph("Sales Summary", heading_style))
    
    # Get completed transactions for the day
    daily_transactions = Transaction.objects.filter(
        status='completed',
        created_at__date=report_date
    )
    
    # If no transactions found, try alternative method
    if daily_transactions.count() == 0:
        start_datetime = timezone.make_aware(datetime.combine(report_date, datetime.min.time()))
        end_datetime = start_datetime + timedelta(days=1)
        daily_transactions = Transaction.objects.filter(
            status='completed',
            created_at__gte=start_datetime,
            created_at__lt=end_datetime
        )
    
    total_transactions = daily_transactions.count()
    
    # Get aggregated values
    revenue_agg = daily_transactions.aggregate(Sum('total_amount'))['total_amount__sum']
    subtotal_agg = daily_transactions.aggregate(Sum('subtotal'))['subtotal__sum']
    vat_agg = daily_transactions.aggregate(Sum('vat_amount'))['vat_amount__sum']
    vatable_agg = daily_transactions.aggregate(Sum('vatable_sale'))['vatable_sale__sum']
    
    # Convert to Decimal, handling None values
    def to_decimal(value):
        if value is None:
            return Decimal('0.00')
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))
    
    total_revenue = to_decimal(revenue_agg)
    total_subtotal = to_decimal(subtotal_agg)
    total_vat = to_decimal(vat_agg)
    total_vatable = to_decimal(vatable_agg)
    
    # Payment method breakdown
    payment_breakdown = daily_transactions.values('payment_method').annotate(
        count=Count('id'),
        total=Sum('total_amount')
    ).order_by('-total')
    
    payment_labels = dict(Transaction.PAYMENT_METHODS)
    
    # Sales summary table
    sales_data = [
        ['Metric', 'Value'],
        ['Total Transactions', f"{total_transactions:,}"],
        ['Total Revenue', f"{currency_symbol}{float(total_revenue):,.2f}"],
        ['Subtotal', f"{currency_symbol}{float(total_subtotal):,.2f}"],
        ['VAT Amount (12%)', f"{currency_symbol}{float(total_vat):,.2f}"],
        ['Vatable Sales', f"{currency_symbol}{float(total_vatable):,.2f}"],
    ]
    
    sales_table = Table(sales_data, colWidths=[3*inch, 2*inch])
    sales_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#283593')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
    ]))
    elements.append(sales_table)
    elements.append(Spacer(1, 0.2*inch))
    
    # Payment method breakdown
    if payment_breakdown:
        elements.append(Paragraph("Payment Method Breakdown", heading_style))
        payment_data = [['Payment Method', 'Count', 'Total Amount']]
        for entry in payment_breakdown:
            method_label = payment_labels.get(entry['payment_method'], entry['payment_method'].title())
            total_amount = entry['total'] if entry['total'] is not None else Decimal('0.00')
            payment_data.append([
                method_label,
                f"{entry['count']:,}",
                f"{currency_symbol}{float(total_amount):,.2f}"
            ])
        
        payment_table = Table(payment_data, colWidths=[2.5*inch, 1.25*inch, 1.25*inch])
        payment_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#283593')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('ALIGN', (1, 1), (2, -1), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
        ]))
        elements.append(payment_table)
        elements.append(Spacer(1, 0.2*inch))
    
    # Top Products Sold
    transaction_ids = daily_transactions.values_list('id', flat=True)
    top_products = TransactionItem.objects.filter(
        transaction_id__in=transaction_ids
    ).values('product_name', 'product_barcode').annotate(
        quantity_sold=Sum('quantity'),
        total_revenue=Sum('total_price')
    ).order_by('-quantity_sold')[:10]
    
    if top_products:
        elements.append(Paragraph("Top Products Sold (Top 10)", heading_style))
        products_data = [['Product Name', 'Barcode', 'Quantity', 'Revenue']]
        for product in top_products:
            quantity = product['quantity_sold'] if product['quantity_sold'] is not None else 0
            revenue = product['total_revenue'] if product['total_revenue'] is not None else Decimal('0.00')
            products_data.append([
                product['product_name'][:30],  # Truncate long names
                product['product_barcode'],
                f"{quantity:,}",
                f"{currency_symbol}{float(revenue):,.2f}"
            ])
        
        products_table = Table(products_data, colWidths=[2*inch, 1*inch, 0.75*inch, 1.25*inch])
        products_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#283593')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (1, -1), 'LEFT'),
            ('ALIGN', (2, 0), (-1, -1), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
        ]))
        elements.append(products_table)
        elements.append(Spacer(1, 0.2*inch))
    
    elements.append(PageBreak())
    
    # ===== STOCK SUMMARY =====
    elements.append(Paragraph("Stock Summary", heading_style))
    
    # Total products
    total_products = Product.objects.filter(is_active=True).count()
    low_stock_count = Product.objects.filter(is_active=True, stock_quantity__lte=F('low_stock_threshold')).exclude(stock_quantity=0).count()
    out_of_stock_count = Product.objects.filter(is_active=True, stock_quantity=0).count()
    
    stock_summary_data = [
        ['Metric', 'Value'],
        ['Total Active Products', f"{total_products:,}"],
        ['Low Stock Items', f"{low_stock_count:,}"],
        ['Out of Stock Items', f"{out_of_stock_count:,}"],
    ]
    
    stock_summary_table = Table(stock_summary_data, colWidths=[3*inch, 2*inch])
    stock_summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#283593')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
    ]))
    elements.append(stock_summary_table)
    elements.append(Spacer(1, 0.2*inch))
    
    # Low Stock Products
    low_stock_products = Product.objects.filter(
        is_active=True,
        stock_quantity__lte=F('low_stock_threshold')
    ).order_by('stock_quantity', 'name')
    
    if low_stock_products.exists():
        elements.append(Paragraph("Low Stock & Out of Stock Products", heading_style))
        low_stock_data = [['Product Name', 'Barcode', 'Current Stock', 'Threshold', 'Status']]
        
        for product in low_stock_products[:50]:  # Limit to 50 for PDF size
            status = "Out of Stock" if product.stock_quantity == 0 else "Low Stock"
            low_stock_data.append([
                product.name[:30],
                product.barcode,
                f"{product.stock_quantity:,}",
                f"{product.low_stock_threshold:,}",
                status
            ])
        
        low_stock_table = Table(low_stock_data, colWidths=[2*inch, 1*inch, 0.75*inch, 0.75*inch, 1*inch])
        low_stock_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#d32f2f')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (1, -1), 'LEFT'),
            ('ALIGN', (2, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
        ]))
        elements.append(low_stock_table)
        elements.append(Spacer(1, 0.2*inch))
    
    # Category Stock Summary
    category_stock = Product.objects.filter(is_active=True).values(
        'category__name'
    ).annotate(
        product_count=Count('id'),
        total_stock=Sum('stock_quantity'),
        low_stock_count=Count('id', filter=Q(stock_quantity__lte=F('low_stock_threshold')))
    ).order_by('category__name')
    
    if category_stock:
        elements.append(Paragraph("Stock by Category", heading_style))
        category_data = [['Category', 'Products', 'Total Stock', 'Low Stock Items']]
        
        for cat in category_stock:
            category_name = cat['category__name'] or 'Uncategorized'
            category_data.append([
                category_name,
                f"{cat['product_count']:,}",
                f"{cat['total_stock']:,}",
                f"{cat['low_stock_count']:,}"
            ])
        
        category_table = Table(category_data, colWidths=[2*inch, 1*inch, 1.25*inch, 1.25*inch])
        category_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#283593')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),
            ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
        ]))
        elements.append(category_table)
        elements.append(Spacer(1, 0.2*inch))
    
    elements.append(PageBreak())
    
    # ===== RECENT TRANSACTIONS =====
    elements.append(Paragraph("Recent Transactions (Last 50)", heading_style))
    
    recent_transactions = list(daily_transactions.order_by('-created_at')[:50])
    
    if recent_transactions:
        transactions_data = [['Transaction #', 'Member', 'Method', 'Amount', 'Time']]
        
        for txn in recent_transactions:
            member_name = txn.member.full_name if txn.member else 'Guest'
            if len(member_name) > 20:
                member_name = member_name[:17] + '...'
            
            method_short = {
                'cash': 'Cash',
                'debit': 'Debit'
            }.get(txn.payment_method, txn.payment_method.title())
            
            time_str = timezone.localtime(txn.created_at).strftime('%H:%M:%S')
            amount = Decimal(str(txn.total_amount)) if txn.total_amount is not None else Decimal('0.00')
            transactions_data.append([
                txn.transaction_number[:15],
                member_name,
                method_short,
                f"{currency_symbol}{float(amount):,.2f}",
                time_str
            ])
        
        txn_table = Table(transactions_data, colWidths=[1.5*inch, 1.5*inch, 0.75*inch, 1*inch, 0.75*inch])
        txn_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#283593')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (1, -1), 'LEFT'),
            ('ALIGN', (2, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            ('FONTSIZE', (0, 1), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
        ]))
        elements.append(txn_table)
    else:
        elements.append(Paragraph("No transactions for this date.", styles['Normal']))
    
    # Build PDF
    doc.build(elements)
    buffer.seek(0)
    
    # Create HTTP response with PDF
    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    filename = f'daily_report_{report_date.strftime("%Y%m%d")}.pdf'
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    
    return response