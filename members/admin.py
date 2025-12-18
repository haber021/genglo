from django.contrib import admin
from django.contrib.auth.models import User
from django.utils import timezone
from .models import MemberType, Member, BalanceTransaction, DeletedMember
from django import forms
from django.utils.html import format_html
from django.contrib import messages


class MemberPinForm(forms.ModelForm):
    pin = forms.CharField(required=False, max_length=4, min_length=4, help_text='Enter 4-digit PIN to set or leave blank to keep.', widget=forms.PasswordInput(render_value=False))

    class Meta:
        model = Member
        fields = '__all__'

    def clean_pin(self):
        pin = self.cleaned_data.get('pin')
        if pin:
            if not pin.isdigit() or len(pin) != 4:
                raise forms.ValidationError('PIN must be exactly 4 digits')
        return pin

    def clean_user(self):
        user = self.cleaned_data.get('user')
        if user:
            # Check if this username is already used by another member
            username = user.username
            existing_members = Member.objects.filter(user__username=username)
            # Exclude current instance if editing
            if self.instance and self.instance.pk:
                existing_members = existing_members.exclude(pk=self.instance.pk)
            
            if existing_members.exists():
                raise forms.ValidationError(
                    f'Username "{username}" is already assigned to another member: {existing_members.first().full_name}'
                )
        return user

    def save(self, commit=True):
        pin = self.cleaned_data.get('pin')
        instance = super().save(commit=False)
        if pin:
            instance.set_pin(pin)
        if commit:
            instance.save()
        return instance


@admin.register(MemberType)
class MemberTypeAdmin(admin.ModelAdmin):
    list_display = ['name', 'is_active', 'created_at']
    list_filter = ['is_active']
    search_fields = ['name']


@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    form = MemberPinForm
    list_display = ['full_name', 'username', 'rfid_card_number', 'role', 'balance', 'is_active', 'pin_set']
    list_filter = ['role', 'is_active', 'member_type']
    search_fields = ['first_name', 'last_name', 'rfid_card_number', 'email', 'user__username']
    readonly_fields = ['created_at', 'updated_at']
    actions = ['soft_delete_selected', 'hard_delete_selected']

    def username(self, obj):
        if obj.user:
            username_value = obj.user.username
            # Check if this username is duplicated (used by multiple members)
            duplicate_count = Member.objects.filter(user__username=username_value).exclude(pk=obj.pk).count()
            
            if duplicate_count > 0:
                # Show in red if duplicate
                return format_html(
                    '<span style="color: red; font-weight: bold;">{}</span>',
                    username_value
                )
            return username_value
        return '-'
    username.short_description = 'Username'
    username.admin_order_field = 'user__username'

    def pin_set(self, obj):
        return bool(obj.pin_hash)
    pin_set.boolean = True
    pin_set.short_description = 'PIN set?'
    
    def delete_model(self, request, obj):
        """Override delete to record deletion and use soft delete."""
        # Record the deletion before soft-deleting
        self._record_deletion(obj, request.user.username)
        # Soft delete: set is_active to False instead of hard deleting
        obj.is_active = False
        obj.save()
        messages.success(request, f'Member "{obj.full_name}" has been soft-deleted (deactivated). Record saved for restoration.')
    
    def delete_queryset(self, request, queryset):
        """Override bulk delete to record deletions and use soft delete."""
        count = 0
        for obj in queryset:
            self._record_deletion(obj, request.user.username)
            obj.is_active = False
            obj.save()
            count += 1
        messages.success(request, f'{count} member(s) have been soft-deleted (deactivated). Records saved for restoration.')
    
    def _record_deletion(self, member, deleted_by_username):
        """Record member data before deletion."""
        DeletedMember.objects.create(
            original_id=member.id,
            rfid_card_number=member.rfid_card_number,
            first_name=member.first_name,
            last_name=member.last_name,
            email=member.email,
            phone=member.phone,
            member_type_name=member.member_type.name if member.member_type else None,
            role=member.role,
            balance=member.balance,
            username=member.user.username if member.user else None,
            pin_hash=member.pin_hash,
            deleted_by=deleted_by_username,
            original_created_at=member.created_at,
            original_updated_at=member.updated_at,
            original_date_joined=member.date_joined,
            original_last_transaction=member.last_transaction,
        )
    
    def soft_delete_selected(self, request, queryset):
        """Custom action for soft delete (recommended)."""
        self.delete_queryset(request, queryset)
    soft_delete_selected.short_description = "Soft delete selected members (recommended - allows restoration)"
    
    def hard_delete_selected(self, request, queryset):
        """Custom action for hard delete (permanent)."""
        if request.POST.get('post'):
            count = 0
            for obj in queryset:
                # Record before hard delete
                self._record_deletion(obj, request.user.username)
                obj.delete()  # Hard delete
                count += 1
            messages.warning(request, f'{count} member(s) have been permanently deleted. Records saved for restoration.')
    hard_delete_selected.short_description = "Hard delete selected members (PERMANENT - use with caution)"


@admin.register(BalanceTransaction)
class BalanceTransactionAdmin(admin.ModelAdmin):
    list_display = ['member', 'transaction_type', 'amount', 'balance_after', 'created_at']
    list_filter = ['transaction_type', 'created_at']
    search_fields = ['member__first_name', 'member__last_name']
    readonly_fields = ['created_at']


@admin.register(DeletedMember)
class DeletedMemberAdmin(admin.ModelAdmin):
    list_display = ['first_name', 'last_name', 'rfid_card_number', 'role', 'deleted_at', 'deleted_by', 'restored']
    list_filter = ['restored', 'deleted_at', 'role']
    search_fields = ['first_name', 'last_name', 'rfid_card_number', 'email', 'username']
    readonly_fields = ['deleted_at', 'original_created_at', 'original_updated_at', 'original_date_joined', 
                       'original_last_transaction', 'restored', 'restored_at', 'restored_by']
    date_hierarchy = 'deleted_at'
    actions = ['restore_selected_members']
    
    def has_add_permission(self, request):
        return False  # Can't manually add deleted members
    
    def restore_selected_members(self, request, queryset):
        """Restore selected deleted members."""
        restored_count = 0
        for deleted_member in queryset.filter(restored=False):
            try:
                # Check if member with same RFID already exists
                if Member.objects.filter(rfid_card_number=deleted_member.rfid_card_number).exists():
                    messages.warning(request, 
                        f'Cannot restore {deleted_member.first_name} {deleted_member.last_name}: '
                        f'Member with RFID {deleted_member.rfid_card_number} already exists.')
                    continue
                
                # Check if email conflicts
                if deleted_member.email and Member.objects.filter(email=deleted_member.email).exists():
                    messages.warning(request,
                        f'Cannot restore {deleted_member.first_name} {deleted_member.last_name}: '
                        f'Member with email {deleted_member.email} already exists.')
                    continue
                
                # Restore member
                member_type = None
                if deleted_member.member_type_name:
                    try:
                        member_type = MemberType.objects.get(name=deleted_member.member_type_name)
                    except MemberType.DoesNotExist:
                        pass
                
                # Create or find user if username was provided
                user = None
                if deleted_member.username:
                    try:
                        user = User.objects.get(username=deleted_member.username)
                    except User.DoesNotExist:
                        pass
                
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
                    updated_at=deleted_member.original_updated_at or timezone.now(),
                )
                
                # Mark as restored
                deleted_member.restored = True
                deleted_member.restored_at = timezone.now()
                deleted_member.restored_by = request.user.username
                deleted_member.save()
                
                restored_count += 1
                messages.success(request, f'Successfully restored: {restored_member.full_name} (RFID: {restored_member.rfid_card_number})')
            except Exception as e:
                messages.error(request, f'Error restoring {deleted_member.first_name} {deleted_member.last_name}: {str(e)}')
        
        if restored_count > 0:
            messages.success(request, f'Successfully restored {restored_count} member(s).')
    
    restore_selected_members.short_description = "Restore selected deleted members"
