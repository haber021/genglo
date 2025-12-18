from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import IsAuthenticated, AllowAny, BasePermission
from rest_framework.response import Response
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from django.contrib.auth import login
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.http import JsonResponse
import json
import logging
from datetime import timedelta
from decimal import Decimal
import time

logger = logging.getLogger(__name__)

from members.models import Member, BalanceTransaction
from transactions.models import Transaction
from .models import FundTransferOTP
from .serializers import (
    MemberSerializer, TransactionSerializer, 
    BalanceTransactionSerializer, AccountSummarySerializer,
    FundTransferSerializer
)
from .email_utils import send_otp_email, send_transfer_completion_emails


class MobileSessionAuthentication(SessionAuthentication):
    """
    Custom session authentication that doesn't enforce CSRF for mobile API endpoints.
    This allows mobile apps to use session-based authentication without CSRF tokens.
    """
    def enforce_csrf(self, request):
        # Don't enforce CSRF for mobile API endpoints
        return


class MobileMemberPermission(BasePermission):
    """
    Custom permission that allows both authenticated users and session-based members
    (members without username who logged in via RFID + PIN)
    """
    def has_permission(self, request, view):
        # Check if user is authenticated (has username)
        if request.user and request.user.is_authenticated:
            return True
        
        # Check if member is authenticated via session (no username)
        if request.session.get('member_id'):
            try:
                member = Member.objects.get(
                    id=request.session['member_id'],
                    is_active=True
                )
                return True
            except Member.DoesNotExist:
                return False
        
        return False


def get_member_from_request(request):
    """
    Helper function to get member from request.
    Supports both authenticated users and session-based members.
    Returns (member, error_response) tuple.
    """
    member = None
    
    # First, try to get member from authenticated user
    if request.user and request.user.is_authenticated:
        try:
            member = Member.objects.get(user=request.user, is_active=True)
        except Member.DoesNotExist:
            return None, Response(
                {'success': False, 'error': 'Member account not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Member.MultipleObjectsReturned:
            member = Member.objects.filter(user=request.user, is_active=True).first()
            if not member:
                return None, Response(
                    {'success': False, 'error': 'Member account not found'},
                    status=status.HTTP_404_NOT_FOUND
                )
    
    # If no user, try to get member from session (for members without username)
    if not member and request.session.get('member_id'):
        try:
            member = Member.objects.get(
                id=request.session['member_id'],
                is_active=True
            )
        except Member.DoesNotExist:
            return None, Response(
                {'success': False, 'error': 'Member account not found or session expired'},
                status=status.HTTP_401_UNAUTHORIZED
            )
    
    if not member:
        return None, Response(
            {'success': False, 'error': 'Authentication required'},
            status=status.HTTP_401_UNAUTHORIZED
        )
    
    return member, None


@csrf_exempt
@require_http_methods(["POST"])
def mobile_login(request):
    """
    Enhanced login endpoint for mobile app using username and PIN
    Expected JSON: {"username": "john_doe", "pin": "1234"}
    For members with role "member" without username, can use RFID + PIN: {"rfid": "123456", "pin": "1234"}
    Returns: JSON response with member info and session
    """
    try:
        # Parse JSON body
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse(
                {'success': False, 'error': 'Invalid JSON format'},
                status=400
            )
        
        username = data.get('username', '').strip()
        pin = data.get('pin', '').strip()
        rfid = data.get('rfid', '').strip()  # Alternative identifier for members without username
        
        if not pin:
            return JsonResponse(
                {'success': False, 'error': 'PIN is required'},
                status=400
            )
        
        # Validate PIN format (should be 4 digits)
        if not pin.isdigit() or len(pin) != 4:
            return JsonResponse(
                {'success': False, 'error': 'PIN must be exactly 4 digits'},
                status=400
            )
        
        member = None
        
        # If username is provided, try to find member by username
        if username:
            try:
                from django.contrib.auth.models import User
                user = User.objects.get(username=username, is_active=True)
            except User.DoesNotExist:
                return JsonResponse(
                    {'success': False, 'error': 'Invalid username or PIN. Please check your credentials and try again.'},
                    status=401
                )
            except User.MultipleObjectsReturned:
                user = User.objects.filter(username=username, is_active=True).first()
                if not user:
                    return JsonResponse(
                        {'success': False, 'error': 'Invalid username or PIN. Please check your credentials and try again.'},
                        status=401
                    )
            
            try:
                member = Member.objects.get(user=user, is_active=True)
            except Member.DoesNotExist:
                return JsonResponse(
                    {'success': False, 'error': 'Member account not found or is inactive. Please contact administrator.'},
                    status=404
                )
            except Member.MultipleObjectsReturned:
                member = Member.objects.filter(user=user, is_active=True).first()
                if not member:
                    return JsonResponse(
                        {'success': False, 'error': 'Member account not found or is inactive. Please contact administrator.'},
                        status=404
                    )
        # If no username but RFID is provided, try to find member by RFID (for members without username)
        elif rfid:
            try:
                member = Member.objects.get(rfid_card_number=rfid, is_active=True)
                # Only allow this for members with role "member" who don't have a username
                if member.role != 'member':
                    return JsonResponse(
                        {'success': False, 'error': 'RFID login is only allowed for members with role "member"'},
                        status=403
                    )
                if member.user is not None and member.user.username:
                    return JsonResponse(
                        {'success': False, 'error': 'Please use username to login'},
                        status=400
                    )
            except Member.DoesNotExist:
                return JsonResponse(
                    {'success': False, 'error': 'Member not found or account is inactive'},
                    status=404
                )
        else:
            return JsonResponse(
                {'success': False, 'error': 'Username or RFID is required'},
                status=400
            )
        
        # Verify PIN with enhanced error handling
        try:
            if not member.check_pin(pin):
                return JsonResponse(
                    {'success': False, 'error': 'Invalid username or PIN. Please check your credentials and try again.'},
                    status=401
                )
        except AttributeError:
            # Member doesn't have PIN set
            return JsonResponse(
                {'success': False, 'error': 'PIN not set for this account. Please contact administrator.'},
                status=400
            )
        except Exception as e:
            import traceback
            print(f"PIN verification error: {str(e)}")
            print(traceback.format_exc())
            return JsonResponse(
                {'success': False, 'error': 'Error verifying PIN. Please try again later.'},
                status=500
            )
        
        # For members with role "member" without username, allow login without Django user authentication
        if member.role == 'member' and (member.user is None or not member.user.username):
            # Ensure session exists and is saved
            if not request.session.session_key:
                request.session.create()
            
            # Store member info in session for members without username
            request.session['member_id'] = member.id
            request.session['member_rfid'] = member.rfid_card_number
            request.session['member_role'] = member.role
            request.session.save()  # Explicitly save session
            
            # Serialize member data
            serializer = MemberSerializer(member)
            
            # Return success response with member info
            response = JsonResponse({
                'success': True,
                'member': serializer.data,
                'message': f'Welcome back, {member.full_name}!',
                'session_id': request.session.session_key
            }, status=200)
            
            # Set session cookie in response with enhanced settings
            if request.session.session_key:
                response.set_cookie(
                    'sessionid',
                    request.session.session_key,
                    max_age=60 * 60 * 24 * 7,  # 7 days
                    httponly=True,
                    samesite='Lax',
                    secure=False  # Set to True in production with HTTPS
                )
            
            # Add connection-friendly headers
            response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            response['Pragma'] = 'no-cache'
            response['Expires'] = '0'
            
            return response
        
        # Authenticate and login the user (for members with username)
        if member.user is None:
            return JsonResponse(
                {'success': False, 'error': 'User account not found'},
                status=404
            )
        
        try:
            login(request, member.user)
            # Ensure session is saved
            request.session.save()
        except Exception as e:
            import traceback
            print(f"Login error: {str(e)}")
            print(traceback.format_exc())
            return JsonResponse(
                {'success': False, 'error': 'Authentication failed. Please try again.'},
                status=500
            )
        
        # Serialize member data
        serializer = MemberSerializer(member)
        
        # Return success response with member info
        response = JsonResponse({
            'success': True,
            'member': serializer.data,
            'message': f'Welcome back, {member.full_name}!',
            'session_id': request.session.session_key
        }, status=200)
        
        # Set session cookie in response with enhanced settings
        if request.session.session_key:
            response.set_cookie(
                'sessionid',
                request.session.session_key,
                max_age=60 * 60 * 24 * 7,  # 7 days
                httponly=True,
                samesite='Lax',
                secure=False  # Set to True in production with HTTPS
            )
        
        # Add connection-friendly headers
        response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response['Pragma'] = 'no-cache'
        response['Expires'] = '0'
        
        return response
        
    except Exception as e:
        # Log the error for debugging (in production, use proper logging)
        import traceback
        print(f"Mobile login error: {str(e)}")
        print(traceback.format_exc())
        
        return JsonResponse(
            {'success': False, 'error': 'An unexpected error occurred. Please try again later.'},
            status=500
        )


@api_view(['GET'])
@permission_classes([MobileMemberPermission])
def account_info(request):
    """
    Get current member's account information
    Requires authentication (user or session-based)
    """
    member, error_response = get_member_from_request(request)
    if error_response:
        return error_response
    
    serializer = MemberSerializer(member)
    response = Response({
        'success': True,
        'member': serializer.data
    })
    
    # Add connection-friendly headers
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    
    return response


@api_view(['GET'])
@permission_classes([MobileMemberPermission])
def account_summary(request):
    """
    Get comprehensive account summary including recent transactions
    Query params: year (default current year), month (default current month, 1-12)
    """
    member, error_response = get_member_from_request(request)
    if error_response:
        return error_response
    
    # Get recent transactions (last 10) - optimized with select_related
    recent_transactions = Transaction.objects.filter(
        member=member,
        status='completed'
    ).select_related('member').prefetch_related('items').order_by('-created_at')[:10]
    
    # Get recent balance transactions (last 10)
    recent_balance_transactions = member.balance_transactions.all().order_by('-created_at')[:10]
    
    # Get month/year from query params or use current month/year
    now = timezone.now()
    year = int(request.query_params.get('year', now.year))
    month = int(request.query_params.get('month', now.month))
    
    # Validate month
    if month < 1 or month > 12:
        month = now.month
    
    # Calculate monthly totals for selected month
    start_of_month = timezone.datetime(year, month, 1, 0, 0, 0, tzinfo=timezone.get_current_timezone())
    # Calculate end of month
    if month == 12:
        end_of_month = timezone.datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=timezone.get_current_timezone())
    else:
        end_of_month = timezone.datetime(year, month + 1, 1, 0, 0, 0, tzinfo=timezone.get_current_timezone())
    
    # Optimize monthly transactions query
    monthly_transactions = Transaction.objects.filter(
        member=member,
        status='completed',
        created_at__gte=start_of_month,
        created_at__lt=end_of_month
    ).select_related('member')
    
    total_spent_this_month = sum(t.total_amount for t in monthly_transactions)
    
    data = {
        'member': MemberSerializer(member).data,
        'recent_transactions': TransactionSerializer(recent_transactions, many=True).data,
        'recent_balance_transactions': BalanceTransactionSerializer(recent_balance_transactions, many=True).data,
        'total_spent_this_month': str(total_spent_this_month),
        'selected_year': year,
        'selected_month': month
    }
    
    response = Response({
        'success': True,
        'summary': data
    })
    
    # Add connection-friendly headers
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    
    return response


@api_view(['GET'])
@permission_classes([MobileMemberPermission])
def transaction_history(request):
    """
    Get transaction history with pagination
    Query params: page (default 1), limit (default 20)
    """
    member, error_response = get_member_from_request(request)
    if error_response:
        return error_response
    
    page = int(request.query_params.get('page', 1))
    limit = int(request.query_params.get('limit', 20))
    offset = (page - 1) * limit
    
    # Optimize query with select_related to reduce database hits
    transactions = Transaction.objects.filter(
        member=member,
        status='completed'
    ).select_related('member').prefetch_related('items').order_by('-created_at')[offset:offset + limit]
    
    total = Transaction.objects.filter(member=member, status='completed').count()
    
    serializer = TransactionSerializer(transactions, many=True)
    response = Response({
        'success': True,
        'transactions': serializer.data,
        'pagination': {
            'page': page,
            'limit': limit,
            'total': total,
            'has_next': offset + limit < total,
            'has_previous': page > 1
        }
    })
    
    # Add connection-friendly headers
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    
    return response


@api_view(['GET'])
@permission_classes([MobileMemberPermission])
def balance_transactions(request):
    """
    Get balance transaction history (deposits, deductions)
    Query params: page (default 1), limit (default 20)
    """
    member, error_response = get_member_from_request(request)
    if error_response:
        return error_response
    
    page = int(request.query_params.get('page', 1))
    limit = int(request.query_params.get('limit', 20))
    offset = (page - 1) * limit
    
    balance_transactions = member.balance_transactions.all().order_by('-created_at')[offset:offset + limit]
    total = member.balance_transactions.count()
    
    serializer = BalanceTransactionSerializer(balance_transactions, many=True)
    response = Response({
        'success': True,
        'balance_transactions': serializer.data,
        'pagination': {
            'page': page,
            'limit': limit,
            'total': total,
            'has_next': offset + limit < total,
            'has_previous': page > 1
        }
    })
    
    # Add connection-friendly headers
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    
    return response


@api_view(['GET'])
@permission_classes([AllowAny])
def health_check(request):
    """
    Health check endpoint for connection testing
    Returns server status and basic info
    """
    try:
        # Test database connection
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        
        response = JsonResponse({
            'success': True,
            'status': 'healthy',
            'timestamp': timezone.now().isoformat(),
            'server_time': int(time.time()),
            'message': 'Server is running and database is accessible'
        }, status=200)
        
        # Add connection-friendly headers
        response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response['Pragma'] = 'no-cache'
        response['Expires'] = '0'
        
        return response
    except Exception as e:
        return JsonResponse({
            'success': False,
            'status': 'unhealthy',
            'error': str(e),
            'timestamp': timezone.now().isoformat()
        }, status=503)


@api_view(['GET'])
@permission_classes([MobileMemberPermission])
def search_member(request):
    """
    Search for a member by RFID card number
    Query params: rfid (required)
    Returns member info if found
    """
    member, error_response = get_member_from_request(request)
    if error_response:
        return error_response
    
    rfid = request.query_params.get('rfid', '').strip()
    
    if not rfid:
        return Response(
            {'success': False, 'error': 'RFID card number is required'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        recipient = Member.objects.get(rfid_card_number=rfid, is_active=True)
        
        # Don't allow transferring to self
        if recipient.id == member.id:
            return Response(
                {'success': False, 'error': 'Cannot transfer funds to yourself'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        serializer = MemberSerializer(recipient)
        return Response({
            'success': True,
            'member': serializer.data
        })
    except Member.DoesNotExist:
        return Response(
            {'success': False, 'error': 'Member not found with the provided RFID card number'},
            status=status.HTTP_404_NOT_FOUND
        )


@csrf_exempt
@api_view(['POST'])
@authentication_classes([MobileSessionAuthentication])
@permission_classes([MobileMemberPermission])
def request_transfer_otp(request):
    """
    Request OTP for fund transfer - sends OTP via email
    Expected JSON: {"recipient_rfid": "123456", "amount": "100.00", "notes": "Optional note"}
    """
    member, error_response = get_member_from_request(request)
    if error_response:
        return error_response
    
    # Validate request data
    serializer = FundTransferSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(
            {'success': False, 'error': serializer.errors},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    recipient_rfid = serializer.validated_data['recipient_rfid'].strip()
    amount = Decimal(str(serializer.validated_data['amount']))
    notes = serializer.validated_data.get('notes', '').strip()
    
    # Validate amount
    if amount <= 0:
        return Response(
            {'success': False, 'error': 'Transfer amount must be greater than zero'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Check if member has sufficient balance
    if member.balance < amount:
        return Response(
            {'success': False, 'error': 'Insufficient balance'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Check if member has email
    if not member.email:
        return Response(
            {'success': False, 'error': 'Email address is required for OTP verification. Please update your profile.'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Find recipient
    try:
        recipient = Member.objects.get(rfid_card_number=recipient_rfid, is_active=True)
    except Member.DoesNotExist:
        return Response(
            {'success': False, 'error': 'Recipient member not found'},
            status=status.HTTP_404_NOT_FOUND
        )
    
    # Don't allow transferring to self
    if recipient.id == member.id:
        return Response(
            {'success': False, 'error': 'Cannot transfer funds to yourself'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        # Create OTP
        otp = FundTransferOTP.create_otp(member, recipient_rfid, amount, notes)
        
        # Send OTP via email asynchronously (non-blocking)
        # This returns immediately without waiting for email to be sent
        send_otp_email(member, recipient, otp.otp_code, amount, notes)
        
        # Return success response immediately
        # Email is being sent in the background
        return Response({
            'success': True,
            'message': f'OTP has been sent to your email ({member.email}). Please check your inbox.',
            'expires_in': 600,  # 10 minutes in seconds
        })
        
    except Exception as e:
        import traceback
        print(f"OTP request error: {str(e)}")
        print(traceback.format_exc())
        return Response(
            {'success': False, 'error': 'Failed to create OTP. Please try again.'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@csrf_exempt
@api_view(['POST'])
@authentication_classes([MobileSessionAuthentication])
@permission_classes([MobileMemberPermission])
def verify_transfer_otp(request):
    """
    Verify OTP and complete fund transfer
    Expected JSON: {"otp_code": "123456"}
    """
    from django.db import transaction as db_transaction
    
    member, error_response = get_member_from_request(request)
    if error_response:
        return error_response
    
    otp_code = request.data.get('otp_code', '').strip()
    
    if not otp_code:
        return Response(
            {'success': False, 'error': 'OTP code is required'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Find valid OTP
    try:
        otp = FundTransferOTP.objects.get(
            member=member,
            otp_code=otp_code,
            is_used=False
        )
    except FundTransferOTP.DoesNotExist:
        return Response(
            {'success': False, 'error': 'Invalid or expired OTP code'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Check if OTP is still valid
    if not otp.is_valid():
        return Response(
            {'success': False, 'error': 'OTP code has expired. Please request a new one.'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Validate amount again (in case balance changed)
    amount = Decimal(str(otp.amount))
    if member.balance < amount:
        return Response(
            {'success': False, 'error': 'Insufficient balance'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Find recipient
    try:
        recipient = Member.objects.get(rfid_card_number=otp.recipient_rfid, is_active=True)
    except Member.DoesNotExist:
        return Response(
            {'success': False, 'error': 'Recipient member not found'},
            status=status.HTTP_404_NOT_FOUND
        )
    
    # Don't allow transferring to self
    if recipient.id == member.id:
        return Response(
            {'success': False, 'error': 'Cannot transfer funds to yourself'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Perform transfer in a database transaction
    try:
        with db_transaction.atomic():
            # Mark OTP as used
            otp.mark_as_used()
            
            # Deduct from sender
            sender_balance_before = Decimal(str(member.balance)).quantize(Decimal('0.01'))
            member.balance = (sender_balance_before - amount).quantize(Decimal('0.01'))
            member.save(update_fields=['balance'])
            member.refresh_from_db()
            sender_balance_after = Decimal(str(member.balance)).quantize(Decimal('0.01'))
            
            # Record sender's deduction transaction
            sender_transaction = BalanceTransaction.objects.create(
                member=member,
                transaction_type='deduction',
                amount=amount,
                balance_before=sender_balance_before,
                balance_after=sender_balance_after,
                notes=f'Fund transfer to {recipient.full_name} ({recipient.rfid_card_number})' + (f' - {otp.notes}' if otp.notes else '')
            )
            
            # Add to recipient
            recipient_balance_before = Decimal(str(recipient.balance)).quantize(Decimal('0.01'))
            recipient.balance = (recipient_balance_before + amount).quantize(Decimal('0.01'))
            recipient.save(update_fields=['balance'])
            recipient.refresh_from_db()
            recipient_balance_after = Decimal(str(recipient.balance)).quantize(Decimal('0.01'))
            
            # Record recipient's deposit transaction
            recipient_transaction = BalanceTransaction.objects.create(
                member=recipient,
                transaction_type='deposit',
                amount=amount,
                balance_before=recipient_balance_before,
                balance_after=recipient_balance_after,
                notes=f'Fund transfer from {member.full_name} ({member.rfid_card_number})' + (f' - {otp.notes}' if otp.notes else '')
            )
            
            # Update last transaction time
            member.last_transaction = timezone.now()
            member.save(update_fields=['last_transaction'])
            recipient.last_transaction = timezone.now()
            recipient.save(update_fields=['last_transaction'])
        
        # Serialize the balance transactions
        sender_transaction_data = BalanceTransactionSerializer(sender_transaction).data
        recipient_transaction_data = BalanceTransactionSerializer(recipient_transaction).data
        
        # Send completion emails to both sender and recipient (async, non-blocking)
        try:
            send_transfer_completion_emails(
                sender=member,
                recipient=recipient,
                amount=amount,
                sender_balance_after=sender_balance_after,
                recipient_balance_after=recipient_balance_after,
                notes=otp.notes,
                transaction_date=sender_transaction.created_at
            )
        except Exception as email_error:
            # Log email error but don't fail the transfer
            import traceback
            logger.warning(f"Failed to send completion emails: {str(email_error)}")
            print(f"Email notification error (transfer still successful): {str(email_error)}")
        
        # Return success response with full transaction details
        return Response({
            'success': True,
            'message': f'Successfully transferred {amount} to {recipient.full_name}',
            'transfer': {
                'id': sender_transaction.id,
                'recipient': {
                    'id': recipient.id,
                    'full_name': recipient.full_name,
                    'rfid_card_number': recipient.rfid_card_number
                },
                'amount': str(amount),
                'sender_balance_before': str(sender_balance_before),
                'sender_balance_after': str(sender_balance_after),
                'notes': otp.notes,
                'created_at': sender_transaction.created_at.isoformat()
            },
            'sender_transaction': sender_transaction_data,
            'recipient_transaction': recipient_transaction_data
        })
        
    except Exception as e:
        import traceback
        print(f"Fund transfer error: {str(e)}")
        print(traceback.format_exc())
        return Response(
            {'success': False, 'error': 'An error occurred during the transfer. Please try again.'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
