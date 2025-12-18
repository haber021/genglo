"""
URL configuration for coop_kiosk project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.shortcuts import render
from kiosk import views as kiosk_views
from admin_panel import views as admin_panel_views
from members import views as members_views


def handler404(request, exception=None):
    """Custom 404 error handler"""
    return render(request, '404.html', status=404)


def catchall_404(request, **kwargs):
    """Wrapper function for catch-all route that accepts any path parameter"""
    return handler404(request)

urlpatterns = [
    # Custom admin URLs must come before admin.site.urls to take precedence
    path('admin/login/', admin_panel_views.redirect_to_root_login, name='admin_login'),
    path('admin/logout/', admin_panel_views.admin_logout, name='admin_logout'),
    path('admin/', admin.site.urls),
    path('', admin_panel_views.root_login, name='root_login'),
    path('kiosk/', kiosk_views.kiosk_home, name='kiosk_home'),
    path('api/scan-product/', kiosk_views.scan_product, name='scan_product'),
    path('api/search-products/', kiosk_views.search_products, name='search_products'),
    path('api/scan-rfid/', kiosk_views.scan_rfid, name='scan_rfid'),
    path('api/process-payment/', kiosk_views.process_payment, name='process_payment'),
    path('api/print-receipt-local/', kiosk_views.print_receipt_local, name='print_receipt_local'),
    # RFID pre-login gate
    path('rfid-gate/', members_views.rfid_gate, name='rfid_gate'),
    path('api/rfid-validate-login/', members_views.api_validate_rfid_login, name='api_rfid_validate_login'),
    path('kiosk/logout/', admin_panel_views.kiosk_logout, name='kiosk_logout'),
    path('dashboard/', admin_panel_views.dashboard, name='dashboard'),
    path('dashboard/inventory/', admin_panel_views.inventory_management, name='inventory_management'),
    path('dashboard/members/', admin_panel_views.member_management, name='member_management'),
    path('dashboard/members/backup/', admin_panel_views.backup_members_data, name='backup_members_data'),
    path('dashboard/members/restore/', admin_panel_views.restore_members_data, name='restore_members_data'),
    path('dashboard/transactions/', admin_panel_views.transaction_history, name='transaction_history'),
    path('api/search-members/', admin_panel_views.api_search_members, name='api_search_members'),
    path('api/members/create/', admin_panel_views.api_create_member, name='api_create_member'),
    path('api/members/update/', admin_panel_views.api_update_member, name='api_update_member'),
    path('api/member-types/create/', admin_panel_views.api_create_member_type, name='api_create_member_type'),
    path('api/member-types/update/', admin_panel_views.api_update_member_type, name='api_update_member_type'),
    path('api/products/create/', admin_panel_views.api_create_product, name='api_create_product'),
    path('api/products/update/', admin_panel_views.api_update_product, name='api_update_product'),
    path('api/categories/create/', admin_panel_views.api_create_category, name='api_create_category'),
    path('api/categories/update/', admin_panel_views.api_update_category, name='api_update_category'),
    path('api/refill-balance/', admin_panel_views.api_refill_balance, name='api_refill_balance'),
    path('api/rfid-login/', admin_panel_views.api_rfid_login, name='api_rfid_login'),
    path('user-choice/', admin_panel_views.user_choice, name='user_choice'),
    path('user-transactions/', admin_panel_views.user_transactions, name='user_transactions'),
    path('process-refund/', admin_panel_views.process_refund, name='process_refund'),
    path('api/search-transactions-for-refund/', admin_panel_views.api_search_transactions_for_refund, name='api_search_transactions_for_refund'),
    path('api/process-refund/', admin_panel_views.api_process_refund, name='api_process_refund'),
    path('api/search-transactions/', admin_panel_views.api_search_transactions, name='api_search_transactions'),
    path('api/get-transaction/<int:transaction_id>/', admin_panel_views.api_get_transaction, name='api_get_transaction'),
    path('api/update-transaction/', admin_panel_views.api_update_transaction, name='api_update_transaction'),
    path('refund-receipt/<int:transaction_id>/', admin_panel_views.view_refund_receipt, name='view_refund_receipt'),
    path('cash-receipt/<int:transaction_id>/', admin_panel_views.view_cash_receipt, name='view_cash_receipt'),
    path('debit-credit-receipt/<int:transaction_id>/', admin_panel_views.view_debit_credit_receipt, name='view_debit_credit_receipt'),
    path('dashboard/generate-report/', admin_panel_views.generate_daily_report_pdf, name='generate_daily_report_pdf'),
    # Mobile API endpoints
    path('api/mobile/', include('mobile_api.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    # Catch-all route for 404 errors in DEBUG mode
    # This ensures custom 404.html is shown even when DEBUG=True
    urlpatterns += [
        path('<path:path>', catchall_404, name='404_catchall'),
    ]

# Custom 404 handler - Django will automatically use this
# The handler404 variable must be defined at the module level
