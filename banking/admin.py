# banking/admin.py
from django.contrib import admin

from .models import Account, Transaction


class AccountAdmin(admin.ModelAdmin):
    list_display = ["owner", "balance", "created_at"]


class TransactionAdmin(admin.ModelAdmin):
    list_display = ["account", "transaction_type", "amount", "timestamp"]
    list_filter = ["transaction_type"]


admin.site.register(Account, AccountAdmin)
admin.site.register(Transaction, TransactionAdmin)
