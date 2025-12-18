from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils import timezone
from django.db.models import Q
from django.db import transaction as db_transaction
from inventory.models import Product, StockTransaction
from members.models import Member, BalanceTransaction
from transactions.models import Transaction, TransactionItem
from decimal import Decimal
import json
import random
import string
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt


def generate_transaction_number():
    timestamp = timezone.now().strftime('%Y%m%d%H%M%S')
    random_str = ''.join(random.choices(string.digits, k=4))
    return f"TXN{timestamp}{random_str}"


from admin_panel.views import member_or_login_required

@member_or_login_required
@ensure_csrf_cookie
def kiosk_home(request):
    # Pass VAT settings so client-side JS can use the same configuration
    context = {
        'VAT_RATE': settings.VAT_RATE,
        'VAT_INCLUSIVE': settings.VAT_INCLUSIVE,
    }
    return render(request, 'kiosk/kiosk.html', context)


@require_http_methods(["POST"])
def scan_product(request):
    try:
        data = json.loads(request.body)
        barcode = data.get('barcode')
        
        if not barcode:
            return JsonResponse({'success': False, 'error': 'Barcode is required'})
        
        try:
            product = Product.objects.get(barcode=barcode, is_active=True)
            
            if product.stock_quantity <= 0:
                # Track failed attempt for out-of-stock product
                from inventory.utils import track_out_of_stock_attempt
                attempt_count, notification_sent = track_out_of_stock_attempt(product)
                return JsonResponse({'success': False, 'error': 'Product is out of stock'})
            
            return JsonResponse({
                'success': True,
                'product': {
                    'id': product.id,
                    'name': product.name,
                    'barcode': product.barcode,
                    'price': str(product.price),
                    'image': product.image.url if product.image else None,
                    'stock': product.stock_quantity,
                }
            })
        except Product.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Product not found'})
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON data'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': 'Server error occurred'})


@require_http_methods(["GET"])
def search_products(request):
    """
    Simple product search endpoint used by the kiosk JS.
    Query parameter: `q` (string). Returns JSON: { results: [ {id,name,barcode,price,stock}, ... ] }
    Matches against name (icontains) and barcode (exact or contains) and only active products.
    """
    q = request.GET.get('q', '')
    q = (q or '').strip()
    if not q or len(q) < 2:
        return JsonResponse({'results': []})

    try:
        # prefer name icontains, but allow barcode matches as well
        qs = Product.objects.filter(is_active=True)
        # If q looks numeric, include barcode matches first
        if q.isdigit():
            qs = qs.filter(Q(barcode__icontains=q) | Q(name__icontains=q))
        else:
            qs = qs.filter(name__icontains=q)

        qs = qs.order_by('name')[:50]

        results = []
        from inventory.utils import track_out_of_stock_attempt
        
        for idx, p in enumerate(qs):
            stock = getattr(p, 'stock_quantity', getattr(p, 'stock', 0))
            
            # Track failed attempt if product is out of stock
            # Only track for exact matches (barcode or name) or top 3 results to avoid spam
            if stock <= 0:
                # Check if it's an exact match (barcode or name)
                is_exact_match = (
                    (q.isdigit() and p.barcode == q) or  # Exact barcode match
                    (p.name.lower() == q.lower())  # Exact name match
                )
                
                # Track if exact match or in top 3 results
                if is_exact_match or idx < 3:
                    track_out_of_stock_attempt(p)
            
            results.append({
                'id': p.id,
                'name': p.name,
                'barcode': p.barcode,
                'price': str(p.price),
                'stock': stock,
            })

        return JsonResponse({'results': results})
    except Exception:
        return JsonResponse({'results': []})


@require_http_methods(["POST"])
def scan_rfid(request):
    try:
        data = json.loads(request.body)
        rfid = data.get('rfid')
        
        if not rfid:
            return JsonResponse({'success': False, 'error': 'RFID is required'})
        
        try:
            member = Member.objects.get(rfid_card_number=rfid, is_active=True)
            
            request.session['kiosk_member_id'] = member.id
            request.session['kiosk_member_rfid'] = member.rfid_card_number
            
            return JsonResponse({
                'success': True,
                'member': {
                    'id': member.id,
                    'name': member.full_name,
                    'rfid': member.rfid_card_number,
                    'balance': str(member.balance),
                    'role': member.role,
                }
            })
        except Member.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Member not found'})
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON data'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': 'Server error occurred'})


@require_http_methods(["POST"])
@db_transaction.atomic
def process_payment(request):
    try:
        data = json.loads(request.body)
        
        member_id = data.get('member_id')
        items = data.get('items', [])
        payment_method = data.get('payment_method')
        
        if not items:
            return JsonResponse({'success': False, 'error': 'No items in cart'})
        
        if not payment_method or payment_method not in ['debit', 'cash']:
            return JsonResponse({'success': False, 'error': 'Invalid payment method'})
        
        member = None
        if member_id:
            try:
                member_id = int(member_id)
            except (ValueError, TypeError):
                return JsonResponse({'success': False, 'error': 'Invalid member ID'})
            
            session_member_id = request.session.get('kiosk_member_id')
            
            if payment_method == 'debit':
                if not session_member_id or session_member_id != member_id:
                    return JsonResponse({'success': False, 'error': 'Member authentication required. Please scan RFID card again.'})
            
            try:
                member = Member.objects.get(id=member_id, is_active=True)
            except Member.DoesNotExist:
                return JsonResponse({'success': False, 'error': 'Member not found or inactive'})
            # For debit payments require PIN validation (unless member is cashier/admin)
            if payment_method == 'debit':
                is_cashier = member.role in ['cashier', 'admin']
                if not is_cashier:
                    pin = data.get('pin')
                    if not pin:
                        return JsonResponse({'success': False, 'error': 'PIN is required for member payments'})
                    if not member.check_pin(pin):
                        return JsonResponse({'success': False, 'error': 'Invalid PIN'})
                # Cashiers and admins can proceed without PIN (direct access)
        else:
            if payment_method == 'debit':
                return JsonResponse({'success': False, 'error': 'Member required for debit payment'})
            # For cash transactions, if user is logged in, try to associate with their member account
            elif payment_method == 'cash' and request.user.is_authenticated:
                try:
                    # Try to get the member associated with the logged-in user
                    member = Member.objects.get(user=request.user, is_active=True)
                except Member.DoesNotExist:
                    # User doesn't have a member account, that's okay for cash transactions
                    pass
                except Member.MultipleObjectsReturned:
                    # Multiple members found, use the first one
                    member = Member.objects.filter(user=request.user, is_active=True).first()
        
        # Validate item data and lock involved product rows to prevent race conditions
        product_ids = []
        for item_data in items:
            if 'product_id' not in item_data or 'quantity' not in item_data:
                return JsonResponse({'success': False, 'error': 'Invalid item data'})
            try:
                quantity = int(item_data['quantity'])
                if quantity <= 0:
                    return JsonResponse({'success': False, 'error': 'Quantity must be a positive number'})
                if quantity > 1000:
                    return JsonResponse({'success': False, 'error': 'Quantity exceeds maximum allowed (1000)'})
                item_data['quantity'] = quantity
            except (ValueError, TypeError):
                return JsonResponse({'success': False, 'error': 'Invalid quantity value'})
            product_ids.append(item_data['product_id'])

        # Lock product rows so stock checks and reductions are consistent under concurrency
        products_qs = Product.objects.select_for_update().filter(id__in=product_ids, is_active=True)
        product_map = {p.id: p for p in products_qs}

        # Ensure all requested products exist and have sufficient stock
        for item_data in items:
            pid = item_data['product_id']
            product = product_map.get(pid)
            if not product:
                return JsonResponse({'success': False, 'error': 'Invalid product'})
            if product.stock_quantity < item_data['quantity']:
                return JsonResponse({'success': False, 'error': f'Insufficient stock for {product.name}'})
        
        transaction = Transaction.objects.create(
            transaction_number=generate_transaction_number(),
            member=member,
            payment_method=payment_method,
            status='pending'
        )
        
        for item_data in items:
            pid = item_data['product_id']
            product = product_map.get(pid) or Product.objects.get(id=pid)
            # record current stock before change
            before_stock = product.stock_quantity

            TransactionItem.objects.create(
                transaction=transaction,
                product=product,
                product_name=product.name,
                product_barcode=product.barcode,
                unit_price=product.price,
                quantity=item_data['quantity']
            )

            # Attempt to reduce stock on the locked product instance
            if product.stock_quantity >= item_data['quantity']:
                product.stock_quantity -= item_data['quantity']
                product.save()
                StockTransaction.objects.create(
                    product=product,
                    transaction_type='out',
                    quantity=item_data['quantity'],
                    stock_before=before_stock,
                    stock_after=product.stock_quantity,
                    notes=f'Sale via kiosk transaction {transaction.transaction_number}'
                )
            else:
                raise Exception(f'Failed to reduce stock for {product.name}')
        
        transaction.calculate_totals()
        
        # Process product fee: deduct 0.5 pesos per product from member and add to account 3247035272
        product_fee_per_item = Decimal('0.50')
        account_phone_product_fee = '3247035272'
        total_product_fee = Decimal('0.00')
        
        # Calculate total number of products (sum of all quantities)
        total_products = sum(item_data['quantity'] for item_data in items)
        total_product_fee = total_products * product_fee_per_item
        
        if total_product_fee > 0:
            try:
                # Find or create the account member for product fees
                account_member_fee = Member.objects.filter(phone=account_phone_product_fee, is_active=True).first()
                
                if not account_member_fee:
                    # Generate a unique RFID for the account
                    base_rfid = f'ACCOUNT_{account_phone_product_fee}'
                    rfid_card_number = base_rfid
                    counter = 1
                    while Member.objects.filter(rfid_card_number=rfid_card_number).exists():
                        rfid_card_number = f'{base_rfid}_{counter}'
                        counter += 1
                    
                    account_member_fee = Member.objects.create(
                        rfid_card_number=rfid_card_number,
                        phone=account_phone_product_fee,
                        first_name='System',
                        last_name='Account',
                        role='member',
                        is_active=True,
                    )
                
                # Add the product fee to the account
                balance_before_fee = Decimal(str(account_member_fee.balance)).quantize(Decimal('0.01'))
                new_balance_fee = (balance_before_fee + total_product_fee).quantize(Decimal('0.01'))
                account_member_fee.balance = new_balance_fee
                account_member_fee.save(update_fields=['balance'])
                
                # Refresh from database
                account_member_fee.refresh_from_db()
                balance_after_fee = Decimal(str(account_member_fee.balance)).quantize(Decimal('0.01'))
                
                # Record the balance transaction
                BalanceTransaction.objects.create(
                    member=account_member_fee,
                    transaction_type='deposit',
                    amount=total_product_fee,
                    balance_before=balance_before_fee,
                    balance_after=balance_after_fee,
                    notes=f'Product fee ({total_products} products x ₱{product_fee_per_item}) for transaction {transaction.transaction_number}'
                )
                
                # Deduct the fee from member's balance if member exists
                if member:
                    member_balance_before_fee = Decimal(str(member.balance)).quantize(Decimal('0.01'))
                    member.balance = (member_balance_before_fee - total_product_fee).quantize(Decimal('0.01'))
                    member.save(update_fields=['balance'])
                    
                    # Record deduction from member
                    BalanceTransaction.objects.create(
                        member=member,
                        transaction_type='deduction',
                        amount=total_product_fee,
                        balance_before=member_balance_before_fee,
                        balance_after=member.balance,
                        notes=f'Product fee ({total_products} products x ₱{product_fee_per_item}) for transaction {transaction.transaction_number}'
                    )
                
                # Terminal output
                print(f"[PRODUCT FEE] ₱{total_product_fee} ({total_products} products x ₱{product_fee_per_item})")
                print(f"  Added to account: {account_phone_product_fee}")
                print(f"  Account Balance: ₱{balance_before_fee} -> ₱{balance_after_fee}")
                if member:
                    print(f"  Deducted from member: {member.full_name}")
                    print(f"  Member Balance: ₱{member_balance_before_fee} -> ₱{member.balance}")
                
            except Exception as e:
                # Log error but don't fail the main transaction
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f'Failed to process product fee for account {account_phone_product_fee}: {str(e)}')
                print(f"[PRODUCT FEE FAILED] Error processing fee: {str(e)}")
        
        # Capture member balances before any changes for transparency in the response
        member_before_balance = None
        if member:
            member_before_balance = getattr(member, 'balance', None)

        if payment_method == 'debit' and member:
            if member.balance >= transaction.total_amount:
                member.deduct_balance(transaction.total_amount)
                transaction.amount_from_balance = transaction.total_amount
                transaction.status = 'completed'
            else:
                # Insufficient balance - return error instead of converting to credit
                transaction.status = 'cancelled'
                transaction.save()
                return JsonResponse({
                    'success': False, 
                    'error': f'Insufficient balance. Available: ₱{member.balance}, Required: ₱{transaction.total_amount}'
                })
            
            # Transfer 0.5 pesos to account 3247035272 for each debit transaction
            transfer_amount = Decimal('0.50')
            account_phone = '3247035272'
            try:
                # Find the account member by phone number
                account_member = Member.objects.filter(phone=account_phone, is_active=True).first()
                
                # If not found, create a new account member
                if not account_member:
                    # Generate a unique RFID for the account
                    base_rfid = f'ACCOUNT_{account_phone}'
                    rfid_card_number = base_rfid
                    counter = 1
                    while Member.objects.filter(rfid_card_number=rfid_card_number).exists():
                        rfid_card_number = f'{base_rfid}_{counter}'
                        counter += 1
                    
                    account_member = Member.objects.create(
                        rfid_card_number=rfid_card_number,
                        phone=account_phone,
                        first_name='System',
                        last_name='Account',
                        role='member',
                        is_active=True,
                    )
                
                # Add the transfer amount to the account
                balance_before = Decimal(str(account_member.balance)).quantize(Decimal('0.01'))
                # Add the amount to the balance
                new_balance = (balance_before + transfer_amount).quantize(Decimal('0.01'))
                account_member.balance = new_balance
                account_member.save(update_fields=['balance'])
                
                # Refresh from database to get the actual saved value
                account_member.refresh_from_db()
                balance_after = Decimal(str(account_member.balance)).quantize(Decimal('0.01'))
                
                # Record the balance transaction
                BalanceTransaction.objects.create(
                    member=account_member,
                    transaction_type='deposit',
                    amount=transfer_amount,
                    balance_before=balance_before,
                    balance_after=balance_after,
                    notes=f'Debit transaction fee for transaction {transaction.transaction_number}'
                )
                
                # Terminal output: Transfer successful
                print(f"[TRANSFER SUCCESS] ₱{transfer_amount} added to account {account_phone}")
                print(f"  Transaction: {transaction.transaction_number}")
                print(f"  Account Balance: ₱{balance_before} -> ₱{balance_after}")
                print(f"  Account Member ID: {account_member.id}, RFID: {account_member.rfid_card_number}")
                
            except Exception as e:
                # Log error but don't fail the main transaction
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f'Failed to process transfer for account {account_phone}: {str(e)}')
                
                # Terminal output: Transfer failed
                print(f"[TRANSFER FAILED] Failed to add ₱{transfer_amount} to account {account_phone}")
                print(f"  Transaction: {transaction.transaction_number}")
                print(f"  Error: {str(e)}")
        
        elif payment_method == 'cash':
            # Get cash amount from request, validate it's sufficient
            cash_amount = data.get('cash_amount')
            if cash_amount is not None:
                try:
                    cash_amount = Decimal(str(cash_amount))
                    if cash_amount < transaction.total_amount:
                        return JsonResponse({
                            'success': False, 
                            'error': f'Insufficient cash. Total: ₱{transaction.total_amount}, Received: ₱{cash_amount}'
                        })
                    transaction.amount_paid = cash_amount
                except (ValueError, TypeError):
                    return JsonResponse({'success': False, 'error': 'Invalid cash amount'})
            else:
                # If no cash_amount provided, assume exact payment
                transaction.amount_paid = transaction.total_amount
            transaction.status = 'completed'
        
        transaction.save()
        
        if member:
            member.last_transaction = timezone.now()
            member.save()
        
        request.session.pop('kiosk_member_id', None)
        request.session.pop('kiosk_member_rfid', None)
        
        # Prepare member summary for response
        member_summary = None
        if member:
            member_summary = {
                'id': member.id,
                'name': member.full_name,
                'balance': str(member.balance),
                'available_balance': str(member.available_balance),
                'balance_before': str(member_before_balance) if member_before_balance is not None else str(member.balance),
                'balance_after': str(member.balance),
            }

        # Calculate change for cash payments
        change_amount = Decimal('0.00')
        if payment_method == 'cash' and transaction.amount_paid > transaction.total_amount:
            change_amount = transaction.amount_paid - transaction.total_amount

        return JsonResponse({
            'success': True,
            'transaction': {
                'id': transaction.id,
                'transaction_number': transaction.transaction_number,
                'payment_method': transaction.payment_method,
                'subtotal': str(transaction.subtotal),
                'vatable_sale': str(transaction.vatable_sale),
                'vat_amount': str(transaction.vat_amount),
                'items': [
                    {
                        'product_name': ti.product_name,
                        'quantity': ti.quantity,
                        'unit_price': str(ti.unit_price),
                        'total_price': str(ti.total_price),
                        'vat_amount': str(ti.vat_amount),
                        'vatable_sale': str(ti.vatable_sale),
                    } for ti in transaction.items.all()
                ],
                'total_amount': str(transaction.total_amount),
                'amount_from_balance': str(transaction.amount_from_balance),
                'amount_paid': str(transaction.amount_paid),
                'change_amount': str(change_amount),
                'member': member_summary,
            }
        })
    
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON data'})
    except Product.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Product not found'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Transaction failed: {str(e)}'})


@csrf_exempt
@require_http_methods(["POST"])
def print_receipt_local(request):
    """
    POST endpoint to print receipts on the server's default Windows printer.
    Expects JSON: { "text": "...receipt text..." } or { "html": "...", "text": "..." }
    If HTML is provided, prints the HTML directly to match the exact same template as manual printing.
    This endpoint attempts to use pywin32 (win32api) or webbrowser. If these are not available or
    printing fails, it returns success: false with an error message.
    """
    try:
        data = json.loads(request.body)
        text = data.get('text', '')
        html = data.get('html', '')
        
        # If HTML is provided, try to print it directly using the template formatting
        # This ensures the refund_receipt.html template is used with proper CSS styling
        if html:
            try:
                import tempfile
                import os
                import subprocess
                import webbrowser
                
                # Create a temporary HTML file with the receipt template
                with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False, encoding='utf-8') as f:
                    f.write(html)
                    temp_file = f.name
                
                try:
                    # Try to print using Windows default browser/print handler
                    # This preserves the CSS formatting from refund_receipt.html
                    os.startfile(temp_file, 'print')
                    # Give it a moment to start printing
                    import time
                    time.sleep(1)
                    return JsonResponse({'success': True, 'message': 'Receipt sent to printer using HTML template'})
                except Exception as e:
                    # Fallback: try using webbrowser
                    try:
                        webbrowser.open(temp_file)
                        import time
                        time.sleep(1)
                        return JsonResponse({'success': True, 'message': 'Receipt opened in browser for printing'})
                    except Exception as e2:
                        # If HTML printing fails, fall through to text extraction
                        pass
                finally:
                    # Clean up temp file after a delay (give print time to start)
                    import threading
                    def cleanup():
                        import time
                        time.sleep(5)
                        try:
                            os.unlink(temp_file)
                        except:
                            pass
                    threading.Thread(target=cleanup, daemon=True).start()
            except Exception as e:
                # If HTML printing fails, fall through to text extraction
                pass
        
        # Use provided text if available (it matches the HTML template exactly)
        # Only extract from HTML if text is not provided
        if not text and html:
            try:
                from html.parser import HTMLParser
                import re
                
                # Extract the receiptPaper element content - this is the same element used in manual print
                # Look for the receiptPaper div (by id or class) in the HTML
                receipt_paper_match = re.search(
                    r'<div[^>]*(?:id|class)=["\'][^"\']*receiptPaper[^"\']*["\'][^>]*>(.*?)</div>\s*(?:</div>|</body>)', 
                    html, 
                    re.DOTALL | re.IGNORECASE
                )
                
                if receipt_paper_match:
                    receipt_content = receipt_paper_match.group(1)
                else:
                    # Fallback: extract from body if receiptPaper not found
                    body_match = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL | re.IGNORECASE)
                    if body_match:
                        receipt_content = body_match.group(1)
                    else:
                        receipt_content = html
                
                # Extract text from HTML, preserving structure and formatting
                class ReceiptTextExtractor(HTMLParser):
                    def __init__(self):
                        super().__init__()
                        self.lines = []
                        self.current_line_parts = []
                        
                    def handle_data(self, data):
                        data = data.strip()
                        if data:
                            self.current_line_parts.append(data)
                            
                    def handle_starttag(self, tag, attrs):
                        attrs_dict = dict(attrs)
                        class_attr = attrs_dict.get('class', '')
                        # Section titles should be on their own line
                        if 'rp-section-title' in class_attr:
                            if self.current_line_parts:
                                self.lines.append(' '.join(self.current_line_parts))
                                self.current_line_parts = []
                        elif tag == 'br':
                            if self.current_line_parts:
                                self.lines.append(' '.join(self.current_line_parts))
                                self.current_line_parts = []
                                
                    def handle_endtag(self, tag):
                        if tag in ['div', 'li', 'p']:
                            if self.current_line_parts:
                                self.lines.append(' '.join(self.current_line_parts))
                                self.current_line_parts = []
                        elif tag == 'ul':
                            if self.current_line_parts:
                                self.lines.append(' '.join(self.current_line_parts))
                                self.current_line_parts = []
                                
                    def get_text(self):
                        if self.current_line_parts:
                            self.lines.append(' '.join(self.current_line_parts))
                        # Filter out empty lines and join with line breaks
                        return '\r\n'.join([line.strip() for line in self.lines if line.strip()])
                
                parser = ReceiptTextExtractor()
                parser.feed(receipt_content)
                extracted_text = parser.get_text()
                
                if extracted_text and len(extracted_text) > 50:
                    text = extracted_text
                else:
                    return JsonResponse({
                        'success': False, 
                        'error': 'Could not extract text content from HTML receipt template'
                    })
                    
            except Exception as e:
                return JsonResponse({
                    'success': False, 
                    'error': f'Failed to extract text from HTML template: {str(e)}'
                })
        
        # Fallback to text printing if HTML not available or HTML printing failed
        if not text:
            return JsonResponse({'success': False, 'error': 'No content available for printing'})

        # Text printing using win32print (for thermal printers that need raw text)
        try:
            import win32print
        except ImportError:
            return JsonResponse({
                'success': False, 
                'error': 'Printing module not available. Please install pywin32: pip install pywin32'
            })

        try:
            # Get default printer
            try:
                printer_name = win32print.GetDefaultPrinter()
            except Exception as e:
                return JsonResponse({
                    'success': False, 
                    'error': f'No default printer found. Please set a default printer in Windows. Error: {str(e)}'
                })

            if not printer_name:
                return JsonResponse({
                    'success': False, 
                    'error': 'No default printer configured. Please set a default printer in Windows.'
                })

            # Open printer
            hPrinter = win32print.OpenPrinter(printer_name)
            try:
                # Start a RAW print job
                job_info = ("KioskReceipt", None, "RAW")
                job_id = win32print.StartDocPrinter(hPrinter, 1, job_info)
                try:
                    win32print.StartPagePrinter(hPrinter)
                    # Ensure text ends with newlines for proper printing
                    print_text = text
                    if not print_text.endswith('\r\n'):
                        print_text += '\r\n\r\n'
                    # Write bytes to printer (encode as utf-8)
                    win32print.WritePrinter(hPrinter, print_text.encode('utf-8'))
                    win32print.EndPagePrinter(hPrinter)
                except Exception as e:
                    # Try to abort the job if page printing fails
                    try:
                        win32print.AbortPrinter(hPrinter)
                    except:
                        pass
                    raise e
                finally:
                    win32print.EndDocPrinter(hPrinter)
            finally:
                win32print.ClosePrinter(hPrinter)

            return JsonResponse({'success': True, 'message': f'Receipt sent to printer: {printer_name}'})
        except Exception as e:
            error_msg = str(e)
            # Provide more helpful error messages
            if 'Access is denied' in error_msg or 'access denied' in error_msg.lower():
                return JsonResponse({
                    'success': False, 
                    'error': 'Access denied to printer. Please check printer permissions or try running the application as administrator.'
                })
            elif 'printer' in error_msg.lower() and 'not found' in error_msg.lower():
                return JsonResponse({
                    'success': False, 
                    'error': 'Printer not found. Please check that the printer is connected and set as default.'
                })
            else:
                return JsonResponse({
                    'success': False, 
                    'error': f'Printing failed: {error_msg}'
                })
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON data received'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Server error: {str(e)}'})
