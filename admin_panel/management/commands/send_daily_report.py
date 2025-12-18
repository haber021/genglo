from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Sum, Count, Q, F
from django.core.mail import EmailMessage
from django.conf import settings
from django.contrib.auth.models import User
from datetime import datetime, timedelta
from decimal import Decimal
import io
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

from transactions.models import Transaction, TransactionItem
from inventory.models import Product, Category
from members.models import Member
from admin_panel.models import SentDailyReport
from admin_panel.utils import get_masked_from_email


class Command(BaseCommand):
    help = 'Generates and emails a daily sales and stock report as PDF'

    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            type=str,
            help='Date for the report (YYYY-MM-DD format). Defaults to today if not specified.',
        )
        parser.add_argument(
            '--to',
            type=str,
            help='Email recipient (overrides settings).',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force sending even if report was already sent for this date and recipient.',
        )

    def get_admin_email(self):
        """Get admin email from database - checks superusers, staff users, and Member admins"""
        # First, try to get superuser email
        superuser = User.objects.filter(is_superuser=True, is_active=True).exclude(email='').first()
        if superuser and superuser.email:
            return superuser.email
        
        # Then try to get staff user email
        staff_user = User.objects.filter(is_staff=True, is_active=True).exclude(email='').first()
        if staff_user and staff_user.email:
            return staff_user.email
        
        # Finally, try to get Member with admin role
        admin_member = Member.objects.filter(role='admin', is_active=True).exclude(email__isnull=True).exclude(email='').first()
        if admin_member and admin_member.email:
            return admin_member.email
        
        # Fall back to settings
        return getattr(settings, 'DAILY_REPORT_EMAIL', getattr(settings, 'ADMIN_EMAIL', 'habervincent21@gmail.com'))

    def handle(self, *args, **options):
        # Determine the date for the report
        if options['date']:
            try:
                report_date = datetime.strptime(options['date'], '%Y-%m-%d').date()
            except ValueError:
                self.stdout.write(self.style.ERROR('Invalid date format. Use YYYY-MM-DD'))
                return
        else:
            # Default to today
            report_date = timezone.now().date()
        
        # Get email recipient - only send to admin email from database
        recipient_email = options.get('to') or self.get_admin_email()

        # Check if report has already been sent for this date and recipient
        force_resend = options.get('force', False)
        if not force_resend:
            already_sent = SentDailyReport.objects.filter(
                report_date=report_date,
                recipient_email=recipient_email
            ).exists()
            
            if already_sent:
                sent_record = SentDailyReport.objects.get(
                    report_date=report_date,
                    recipient_email=recipient_email
                )
                self.stdout.write(self.style.WARNING(
                    f'Report for {report_date} has already been sent to {recipient_email} '
                    f'on {sent_record.sent_at.strftime("%Y-%m-%d %H:%M:%S")}. '
                    f'Use --force to resend.'
                ))
                return

        self.stdout.write(f'Generating daily report for {report_date}...')
        
        # Check if there are any completed transactions for this date
        has_transactions = Transaction.objects.filter(
            status='completed',
            created_at__date=report_date
        ).exists()
        
        if not has_transactions:
            # Suggest the most recent date with transactions
            latest_date = Transaction.objects.filter(
                status='completed'
            ).dates('created_at', 'day', order='DESC').first()
            
            if latest_date:
                self.stdout.write(self.style.WARNING(
                    f'No completed transactions found for {report_date}. '
                    f'Most recent date with transactions: {latest_date}. '
                    f'Consider using --date {latest_date} to generate a report for that date.'
                ))

        # Generate PDF
        pdf_buffer = self.generate_pdf(report_date)

        # Send email
        try:
            self.send_email(pdf_buffer, report_date, recipient_email)
            
            # Record that the report was sent (update if forcing resend, create if new)
            SentDailyReport.objects.update_or_create(
                report_date=report_date,
                recipient_email=recipient_email,
                defaults={'sent_at': timezone.now()}
            )
            
            self.stdout.write(self.style.SUCCESS(f'Successfully sent daily report to {recipient_email}'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error sending email: {str(e)}'))
            raise

    def generate_pdf(self, report_date):
        """Generate PDF report for the given date"""
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
        
        # Debug: Check all transactions first
        all_transactions_count = Transaction.objects.count()
        all_completed_count = Transaction.objects.filter(status='completed').count()
        self.stdout.write(f'Total transactions in DB: {all_transactions_count}')
        self.stdout.write(f'Total completed transactions: {all_completed_count}')
        
        # Get completed transactions for the day using date filter
        # Method 1: Using __date lookup (recommended)
        daily_transactions = Transaction.objects.filter(
            status='completed',
            created_at__date=report_date
        )
        
        transaction_count = daily_transactions.count()
        self.stdout.write(f'Found {transaction_count} completed transactions for {report_date} using __date filter')
        
        # If no transactions found, try alternative methods and show debug info
        if transaction_count == 0:
            # Check what dates actually exist in the database
            existing_dates = Transaction.objects.filter(
                status='completed'
            ).dates('created_at', 'day', order='DESC')[:10]
            
            self.stdout.write(f'Available dates with completed transactions: {list(existing_dates)}')
            
            # Also try a range query as fallback (accounting for timezone)
            start_datetime = timezone.make_aware(datetime.combine(report_date, datetime.min.time()))
            end_datetime = start_datetime + timedelta(days=1)
            
            daily_transactions_range = Transaction.objects.filter(
                status='completed',
                created_at__gte=start_datetime,
                created_at__lt=end_datetime
            )
            
            range_count = daily_transactions_range.count()
            self.stdout.write(f'Found {range_count} completed transactions using datetime range filter')
            
            if range_count > 0:
                daily_transactions = daily_transactions_range
                transaction_count = range_count
        
        total_transactions = daily_transactions.count()
        
        # Debug: Show sample transaction dates if found
        if total_transactions > 0:
            sample_txn = daily_transactions.first()
            self.stdout.write(f'Sample transaction date: {sample_txn.created_at} (status: {sample_txn.status})')
            self.stdout.write(f'Sample transaction amount: {sample_txn.total_amount}')
        
        # Get aggregated values, ensuring they're Decimal type
        revenue_agg = daily_transactions.aggregate(Sum('total_amount'))['total_amount__sum']
        subtotal_agg = daily_transactions.aggregate(Sum('subtotal'))['subtotal__sum']
        vat_agg = daily_transactions.aggregate(Sum('vat_amount'))['vat_amount__sum']
        vatable_agg = daily_transactions.aggregate(Sum('vatable_sale'))['vatable_sale__sum']
        
        # Debug: Show raw aggregated values
        self.stdout.write(f'Raw revenue_agg: {revenue_agg}')
        self.stdout.write(f'Raw subtotal_agg: {subtotal_agg}')
        
        # Convert to Decimal, handling None values and existing Decimal values
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
        
        # Sales summary table - format values properly
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
            payment_data = [['Payment Method', 'Total Amount']]
            for entry in payment_breakdown:
                method_label = payment_labels.get(entry['payment_method'], entry['payment_method'].title())
                total_amount = entry['total'] if entry['total'] is not None else Decimal('0.00')
                payment_data.append([
                    method_label,
                    f"{currency_symbol}{float(total_amount):,.2f}"
                ])
            
            payment_table = Table(payment_data, colWidths=[3*inch, 2*inch])
            payment_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#283593')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (0, -1), 'LEFT'),
                ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 11),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
                ('GRID', (0, 0), (-1, -1), 1, colors.grey),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
            ]))
            elements.append(payment_table)
            elements.append(Spacer(1, 0.2*inch))
        
        # Top Products Sold - use the same daily_transactions queryset for consistency
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
        
        # Debug: Show how many transactions we're including
        self.stdout.write(f'Including {len(recent_transactions)} transactions in recent transactions list')
        
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
        return buffer

    def send_email(self, pdf_buffer, report_date, recipient_email):
        """Send the PDF report via email"""
        date_str = report_date.strftime('%B %d, %Y')
        subject = f'Daily Sales & Stock Report - {date_str}'
        
        body = f"""
Dear Administrator,

Please find attached the daily sales and stock report for {date_str}.

This report includes:
- Sales summary and statistics
- Payment method breakdown
- Top products sold
- Stock levels and low stock alerts
- Recent transactions

Best regards,
Cooperative Kiosk System
        """.strip()
        
        email = EmailMessage(
            subject=subject,
            body=body,
            from_email=get_masked_from_email(),
            to=[recipient_email],
        )
        
        # Attach PDF
        filename = f'daily_report_{report_date.strftime("%Y%m%d")}.pdf'
        email.attach(filename, pdf_buffer.getvalue(), 'application/pdf')
        
        email.send()

