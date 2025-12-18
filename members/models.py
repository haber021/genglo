from django.db import models
from django.contrib.auth.models import User
from django.contrib.auth.hashers import make_password, check_password


class MemberType(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Member Type"
        verbose_name_plural = "Member Types"


class Member(models.Model):
    ROLE_CHOICES = [
        ('admin', 'Admin'),
        ('cashier', 'Cashier'),
        ('staff', 'Staff'),
        ('member', 'Member'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, null=True, blank=True)
    rfid_card_number = models.CharField(max_length=50, unique=True)
    # store a hashed 4-digit PIN for member security (not plaintext)
    pin_hash = models.CharField(max_length=128, blank=True, null=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(unique=True, null=True, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    member_type = models.ForeignKey(MemberType, on_delete=models.SET_NULL, null=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='member')
    
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    
    is_active = models.BooleanField(default=True)
    date_joined = models.DateTimeField(auto_now_add=True)
    last_transaction = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.rfid_card_number})"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def available_balance(self):
        return self.balance

    def add_balance(self, amount):
        self.balance += amount
        self.save()

    def deduct_balance(self, amount):
        if self.balance >= amount:
            self.balance -= amount
            self.save()
            return True
        return False

    def set_pin(self, pin: str):
        """Set a 4-digit PIN for the member. PIN is hashed using Django's password hasher.

        Raises ValueError if PIN is not exactly 4 digits.
        """
        if not isinstance(pin, str) or not pin.isdigit() or len(pin) != 4:
            raise ValueError('PIN must be a 4-digit string')
        self.pin_hash = make_password(pin)
        self.save()

    def check_pin(self, pin: str) -> bool:
        """Validate a candidate PIN against stored hash."""
        if not self.pin_hash:
            return False
        return check_password(pin, self.pin_hash)

    class Meta:
        verbose_name = "Member"
        verbose_name_plural = "Members"
        ordering = ['-date_joined']


class BalanceTransaction(models.Model):
    TRANSACTION_TYPES = [
        ('deposit', 'Deposit'),
        ('deduction', 'Deduction'),
    ]

    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='balance_transactions')
    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPES)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    balance_before = models.DecimalField(max_digits=10, decimal_places=2)
    balance_after = models.DecimalField(max_digits=10, decimal_places=2)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.member.full_name} - {self.transaction_type} - {self.amount}"

    class Meta:
        verbose_name = "Balance Transaction"
        verbose_name_plural = "Balance Transactions"
        ordering = ['-created_at']


class DeletedMember(models.Model):
    """Record of deleted members for easy restoration."""
    # Store original member data
    original_id = models.IntegerField(help_text="Original member ID before deletion")
    rfid_card_number = models.CharField(max_length=50)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(null=True, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    member_type_name = models.CharField(max_length=100, blank=True, null=True)
    role = models.CharField(max_length=20)
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    username = models.CharField(max_length=150, blank=True, null=True)
    pin_hash = models.CharField(max_length=128, blank=True, null=True)
    
    # Metadata
    deleted_at = models.DateTimeField(auto_now_add=True)
    deleted_by = models.CharField(max_length=150, blank=True, null=True, help_text="Username of person who deleted")
    deletion_reason = models.TextField(blank=True, help_text="Optional reason for deletion")
    
    # Store original timestamps
    original_created_at = models.DateTimeField(null=True, blank=True)
    original_updated_at = models.DateTimeField(null=True, blank=True)
    original_date_joined = models.DateTimeField(null=True, blank=True)
    original_last_transaction = models.DateTimeField(null=True, blank=True)
    
    # Restoration tracking
    restored = models.BooleanField(default=False)
    restored_at = models.DateTimeField(null=True, blank=True)
    restored_by = models.CharField(max_length=150, blank=True, null=True)
    
    class Meta:
        verbose_name = "Deleted Member"
        verbose_name_plural = "Deleted Members"
        ordering = ['-deleted_at']
    
    def __str__(self):
        return f"Deleted: {self.first_name} {self.last_name} ({self.rfid_card_number}) - {self.deleted_at}"