# banking/management/commands/seed_users.py
import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from banking.models import get_or_create_account

DEMO_USERS = ["alice", "bob", "harry", "hermione", "ron"]
PASSWORD = "testpass123"

# The demo customers keep their well-known password -- they only hold
# pretend money. The superuser can edit anyone's balance, and this password
# is printed in a public tutorial, so in production it comes from the
# environment instead.
ADMIN_PASSWORD = os.environ.get("DJANGO_ADMIN_PASSWORD", PASSWORD)


class Command(BaseCommand):
    help = "Create demo users (password: testpass123), each with a bank account."

    def handle(self, *args, **options):
        User = get_user_model()
        for username in DEMO_USERS:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={"email": f"{username}@email.com"},
            )
            if created:
                user.set_password(PASSWORD)
                user.save()
                self.stdout.write(self.style.SUCCESS(f'Created user "{username}"'))
            else:
                self.stdout.write(f'User "{username}" already exists')
            get_or_create_account(user)

        if not User.objects.filter(username="admin").exists():
            User.objects.create_superuser("admin", "admin@email.com", ADMIN_PASSWORD)
            self.stdout.write(self.style.SUCCESS('Created superuser "admin"'))
