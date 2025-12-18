"""
Email utility module for asynchronous email sending.
This module provides functions to send emails in the background without blocking the API response.
Uses threading for async execution and includes retry logic for reliability.
"""
import threading
import logging
import time
from django.core.mail import EmailMessage, get_connection
from django.conf import settings

logger = logging.getLogger(__name__)

# Email sending configuration
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds between retries


def send_email_async(subject, body, recipient_email, from_email=None, max_retries=MAX_RETRIES):
    """
    Send an email asynchronously in a background thread.
    This function returns immediately without waiting for the email to be sent.
    Includes retry logic for better reliability.
    
    Args:
        subject: Email subject
        body: Email body (plain text)
        recipient_email: Recipient email address (string or list)
        from_email: Sender email (defaults to DEFAULT_FROM_EMAIL from settings)
        max_retries: Maximum number of retry attempts (default: 3)
    
    Returns:
        threading.Thread: The thread object (can be used to check status if needed)
    """
    if from_email is None:
        from_email = settings.DEFAULT_FROM_EMAIL
    
    def _send_email_with_retry():
        """Internal function that runs in the background thread with retry logic"""
        recipients = [recipient_email] if isinstance(recipient_email, str) else recipient_email
        
        for attempt in range(1, max_retries + 1):
            try:
                # Use get_connection() with optimized settings for faster delivery
                # Connection timeout is set in settings.EMAIL_TIMEOUT
                connection = get_connection(
                    fail_silently=False,
                    use_tls=settings.EMAIL_USE_TLS,
                    timeout=getattr(settings, 'EMAIL_TIMEOUT', 10),
                )
                
                email = EmailMessage(
                    subject=subject,
                    body=body,
                    from_email=from_email,
                    to=recipients,
                    connection=connection,
                )
                
                # Send email (non-blocking in background thread)
                email.send()
                
                # Close connection immediately after sending
                connection.close()
                
                logger.info(f"Email sent successfully to {recipient_email} (attempt {attempt})")
                return  # Success - exit function
                
            except Exception as e:
                error_msg = str(e)
                logger.warning(
                    f"Email send attempt {attempt}/{max_retries} failed for {recipient_email}: {error_msg}"
                )
                
                # If this is not the last attempt, wait before retrying
                if attempt < max_retries:
                    time.sleep(RETRY_DELAY * attempt)  # Exponential backoff
                else:
                    # Last attempt failed - log error
                    logger.error(
                        f"Failed to send email to {recipient_email} after {max_retries} attempts: {error_msg}",
                        exc_info=True
                    )
                    # Don't raise exception - email failures shouldn't crash the app
    
    # Start email sending in a background thread
    thread = threading.Thread(target=_send_email_with_retry, daemon=True)
    thread.start()
    return thread


def send_otp_email(member, recipient, otp_code, amount, notes=''):
    """
    Send OTP email for fund transfer verification.
    This is a convenience wrapper around send_email_async.
    Optimized for fast delivery with async execution.
    
    Args:
        member: Member object (sender)
        recipient: Member object (recipient)
        otp_code: 6-digit OTP code
        amount: Transfer amount (Decimal)
        notes: Optional transfer notes
    
    Returns:
        threading.Thread: The thread object
    """
    subject = 'Fund Transfer Verification Code'
    
    # Optimized email body - concise but informative
    body = f"""Dear {member.full_name},

Fund Transfer Verification

Recipient: {recipient.full_name} ({recipient.rfid_card_number})
Amount: ₱{amount:,.2f}
{('Notes: ' + notes) if notes else ''}

Your verification code: {otp_code}

Valid for 10 minutes. Do not share this code.

If you didn't request this, please contact support immediately.

Best regards,
Cooperative Kiosk System""".strip()
    
    # Log email sending start (for monitoring)
    logger.info(f"Initiating OTP email send to {member.email} for transfer of ₱{amount:,.2f}")
    
    return send_email_async(subject, body, member.email)


def send_transfer_completion_emails(sender, recipient, amount, sender_balance_after, recipient_balance_after, notes='', transaction_date=None):
    """
    Send transfer completion emails to both sender and receiver.
    This function sends two emails asynchronously - one to sender and one to recipient.
    
    Args:
        sender: Member object (sender)
        recipient: Member object (recipient)
        amount: Transfer amount (Decimal)
        sender_balance_after: Sender's balance after transfer
        recipient_balance_after: Recipient's balance after transfer
        notes: Optional transfer notes
        transaction_date: Transaction date/time (optional)
    
    Returns:
        tuple: (sender_thread, recipient_thread) - Thread objects for both emails
    """
    from django.utils import timezone
    from datetime import datetime
    
    if transaction_date is None:
        transaction_date = timezone.now()
    
    # Format transaction date
    if isinstance(transaction_date, str):
        try:
            transaction_date = datetime.fromisoformat(transaction_date.replace('Z', '+00:00'))
        except:
            transaction_date = timezone.now()
    
    date_str = transaction_date.strftime('%B %d, %Y at %I:%M %p')
    
    # Send email to SENDER
    sender_subject = 'Fund Transfer Completed - Money Sent'
    sender_body = f"""Dear {sender.full_name},

Your fund transfer has been completed successfully.

Transfer Details:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Amount Sent: ₱{amount:,.2f}
Recipient: {recipient.full_name}
Recipient RFID: {recipient.rfid_card_number}
{('Notes: ' + notes) if notes else ''}
Transaction Date: {date_str}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Your Account Balance: ₱{sender_balance_after:,.2f}

This transaction has been recorded in your account history.

If you did not authorize this transfer, please contact support immediately.

Best regards,
Cooperative Kiosk System""".strip()
    
    # Send email to RECIPIENT
    recipient_subject = 'Fund Transfer Received - Money Received'
    recipient_body = f"""Dear {recipient.full_name},

You have received a fund transfer.

Transfer Details:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Amount Received: ₱{amount:,.2f}
Sender: {sender.full_name}
Sender RFID: {sender.rfid_card_number}
{('Notes: ' + notes) if notes else ''}
Transaction Date: {date_str}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Your Account Balance: ₱{recipient_balance_after:,.2f}

The funds have been added to your account and are available for use.

Best regards,
Cooperative Kiosk System""".strip()
    
    # Send both emails asynchronously
    sender_thread = None
    recipient_thread = None
    
    # Send to sender if they have email
    if sender.email:
        logger.info(f"Sending transfer completion email to sender: {sender.email}")
        sender_thread = send_email_async(sender_subject, sender_body, sender.email)
    
    # Send to recipient if they have email
    if recipient.email:
        logger.info(f"Sending transfer completion email to recipient: {recipient.email}")
        recipient_thread = send_email_async(recipient_subject, recipient_body, recipient.email)
    
    return sender_thread, recipient_thread

