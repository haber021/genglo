from django.db import models
from django.utils import timezone
from datetime import timedelta
import random
import string


class FundTransferOTP(models.Model):
    """Temporary OTP storage for fund transfer verification"""
    member = models.ForeignKey('members.Member', on_delete=models.CASCADE, related_name='transfer_otps')
    recipient_rfid = models.CharField(max_length=50)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    notes = models.TextField(blank=True)
    otp_code = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)
    verified_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        verbose_name = "Fund Transfer OTP"
        verbose_name_plural = "Fund Transfer OTPs"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['member', 'otp_code', 'is_used']),
            models.Index(fields=['expires_at']),
        ]
    
    @classmethod
    def generate_otp(cls):
        """Generate a 6-digit OTP code"""
        return ''.join(random.choices(string.digits, k=6))
    
    @classmethod
    def create_otp(cls, member, recipient_rfid, amount, notes=''):
        """Create a new OTP for fund transfer"""
        # Delete any existing unused OTPs for this member
        cls.objects.filter(member=member, is_used=False).delete()
        
        # Generate OTP
        otp_code = cls.generate_otp()
        
        # Set expiration to 10 minutes from now
        expires_at = timezone.now() + timedelta(minutes=10)
        
        # Create OTP record
        otp = cls.objects.create(
            member=member,
            recipient_rfid=recipient_rfid,
            amount=amount,
            notes=notes,
            otp_code=otp_code,
            expires_at=expires_at
        )
        
        return otp
    
    def is_valid(self):
        """Check if OTP is still valid (not used and not expired)"""
        if self.is_used:
            return False
        if timezone.now() > self.expires_at:
            return False
        return True
    
    def mark_as_used(self):
        """Mark OTP as used"""
        self.is_used = True
        self.verified_at = timezone.now()
        self.save(update_fields=['is_used', 'verified_at'])
