from rest_framework import serializers
from members.models import Member, BalanceTransaction
from transactions.models import Transaction, TransactionItem


class MemberSerializer(serializers.ModelSerializer):
    """Serializer for Member account information"""
    full_name = serializers.ReadOnlyField()
    member_type_name = serializers.SerializerMethodField()
    
    class Meta:
        model = Member
        fields = [
            'id', 'rfid_card_number', 'first_name', 'last_name', 
            'full_name', 'email', 'phone', 'member_type_name',
            'balance',
            'is_active', 'date_joined', 'last_transaction'
        ]
        read_only_fields = ['id', 'rfid_card_number', 'date_joined', 'last_transaction']
    
    def get_member_type_name(self, obj):
        return obj.member_type.name if obj.member_type else None


class BalanceTransactionSerializer(serializers.ModelSerializer):
    """Serializer for balance transactions"""
    transaction_type_display = serializers.CharField(source='get_transaction_type_display', read_only=True)
    
    class Meta:
        model = BalanceTransaction
        fields = [
            'id', 'transaction_type', 'transaction_type_display',
            'amount', 'balance_before', 'balance_after',
            'notes', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']


class TransactionItemSerializer(serializers.ModelSerializer):
    """Serializer for transaction items"""
    class Meta:
        model = TransactionItem
        fields = [
            'id', 'product_name', 'product_barcode',
            'unit_price', 'quantity', 'total_price',
            'vat_amount', 'vatable_sale', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']


class TransactionSerializer(serializers.ModelSerializer):
    """Serializer for transactions"""
    payment_method_display = serializers.CharField(source='get_payment_method_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    items = TransactionItemSerializer(many=True, read_only=True)
    
    class Meta:
        model = Transaction
        fields = [
            'id', 'transaction_number', 'subtotal', 'vatable_sale',
            'vat_amount', 'total_amount', 'payment_method', 'payment_method_display',
            'amount_paid', 'amount_from_balance',
            'status', 'status_display',
            'notes', 'created_at', 'items'
        ]
        read_only_fields = ['id', 'transaction_number', 'created_at']


class AccountSummarySerializer(serializers.Serializer):
    """Serializer for account summary"""
    member = MemberSerializer()
    recent_transactions = TransactionSerializer(many=True)
    recent_balance_transactions = BalanceTransactionSerializer(many=True)
    total_spent_this_month = serializers.DecimalField(max_digits=10, decimal_places=2)


class FundTransferSerializer(serializers.Serializer):
    """Serializer for fund transfer requests"""
    from decimal import Decimal
    recipient_rfid = serializers.CharField(required=True, help_text="RFID card number of recipient")
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, required=True, min_value=Decimal('0.01'))
    notes = serializers.CharField(required=False, allow_blank=True, max_length=500)
