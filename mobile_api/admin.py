from django.contrib import admin
from .models import FundTransferOTP


@admin.register(FundTransferOTP)
class FundTransferOTPAdmin(admin.ModelAdmin):
    list_display = ['member', 'recipient_rfid', 'amount', 'otp_code', 'is_used', 'created_at', 'expires_at']
    list_filter = ['is_used', 'created_at', 'expires_at']
    search_fields = ['member__first_name', 'member__last_name', 'member__rfid_card_number', 'recipient_rfid', 'otp_code']
    readonly_fields = ['otp_code', 'created_at', 'expires_at', 'verified_at']
    ordering = ['-created_at']
    
    fieldsets = (
        ('Transfer Information', {
            'fields': ('member', 'recipient_rfid', 'amount', 'notes')
        }),
        ('OTP Information', {
            'fields': ('otp_code', 'is_used', 'created_at', 'expires_at', 'verified_at')
        }),
    )
