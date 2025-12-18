from django.db import models
from members.models import Member
from inventory.models import Product
from django.conf import settings
from decimal import Decimal


class Transaction(models.Model):
    PAYMENT_METHODS = [
        ('debit', 'Debit (Member Account)'),
        ('cash', 'Cash'),
    ]

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]

    transaction_number = models.CharField(max_length=50, unique=True)
    member = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, blank=True, related_name='transactions')
    
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    # Vatable sale is the portion of the subtotal that is subject to VAT
    vatable_sale = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    vat_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHODS)
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    amount_from_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.transaction_number} - {self.member.full_name if self.member else 'Guest'}"

    def calculate_totals(self):
        # Sum per-item totals and per-item VAT amounts to avoid mismatches.
        # Sum per-item totals
        subtotal = sum((item.total_price for item in self.items.all()), Decimal('0.00'))
        vat_amount = sum((item.vat_amount for item in self.items.all()), Decimal('0.00'))
        vatable_sale = sum((item.vatable_sale for item in self.items.all()), Decimal('0.00'))

        # total = vat_total + vatable_sale (per your formula)
        total_amount = vat_amount + vatable_sale

        # Quantize to 2 decimal places (currency)
        self.subtotal = Decimal(subtotal).quantize(Decimal('0.01'))
        self.vatable_sale = Decimal(vatable_sale).quantize(Decimal('0.01'))
        self.vat_amount = Decimal(vat_amount).quantize(Decimal('0.01'))
        self.total_amount = Decimal(total_amount).quantize(Decimal('0.01'))
        self.save()

    class Meta:
        verbose_name = "Transaction"
        verbose_name_plural = "Transactions"
        ordering = ['-created_at']


class TransactionItem(models.Model):
    transaction = models.ForeignKey(Transaction, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    
    product_name = models.CharField(max_length=200)
    product_barcode = models.CharField(max_length=100)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.IntegerField(default=1)
    total_price = models.DecimalField(max_digits=10, decimal_places=2)
    vat_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    vatable_sale = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        # Calculate total price and VAT per line item using Decimal arithmetic
        self.total_price = (self.unit_price * Decimal(self.quantity)).quantize(Decimal('0.01'))

        vat_rate = Decimal(str(settings.VAT_RATE))

        # Apply formula requested:
        # vat_total = product * vat_rate
        # vatable_sale = product - vat_total
        vat = (self.total_price * vat_rate)
        vatable = (self.total_price - vat)

        self.vat_amount = vat.quantize(Decimal('0.01'))
        self.vatable_sale = vatable.quantize(Decimal('0.01'))

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.product_name} x {self.quantity}"

    class Meta:
        verbose_name = "Transaction Item"
        verbose_name_plural = "Transaction Items"
