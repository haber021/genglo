from django.urls import path
from . import views

app_name = 'mobile_api'

urlpatterns = [
    path('health/', views.health_check, name='health_check'),
    path('login/', views.mobile_login, name='mobile_login'),
    path('account/', views.account_info, name='account_info'),
    path('account/summary/', views.account_summary, name='account_summary'),
    path('transactions/', views.transaction_history, name='transaction_history'),
    path('balance-transactions/', views.balance_transactions, name='balance_transactions'),
    path('search-member/', views.search_member, name='search_member'),
    path('fund-transfer/request-otp/', views.request_transfer_otp, name='request_transfer_otp'),
    path('fund-transfer/verify-otp/', views.verify_transfer_otp, name='verify_transfer_otp'),
]

