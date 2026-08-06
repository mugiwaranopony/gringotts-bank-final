# banking/test_concurrency.py
"""Two requests hitting the same account at the same moment, for real.

These use TransactionTestCase (not TestCase) because each thread needs its
own real database transaction -- TestCase wraps the whole test in one
transaction, which would hide exactly the behaviour we want to observe.
"""

import threading
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection, connections
from django.test import TransactionTestCase

from .models import (
    WELCOME_BONUS,
    Account,
    InsufficientFunds,
    Transaction,
    get_or_create_account,
    transfer_money,
    withdraw_money,
)

User = get_user_model()


def run_together(work, times=2):
    """Run `work` in `times` threads, all released at the same instant."""
    barrier = threading.Barrier(times)
    results = []
    lock = threading.Lock()

    def target():
        barrier.wait()
        try:
            work()
            outcome = "ok"
        except InsufficientFunds:
            outcome = "rejected"
        except Exception as exc:  # database errors, deadlocks, ...
            outcome = f"{type(exc).__name__}: {exc}"
        finally:
            connections.close_all()
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=target) for _ in range(times)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return results


class ConcurrentWithdrawTests(TransactionTestCase):
    def setUp(self):
        self.alice = get_or_create_account(User.objects.create_user("alice"))

    def test_two_simultaneous_withdrawals_cannot_overdraw(self):
        """$1000 in the account, two threads each try to take $600."""
        results = run_together(
            lambda: withdraw_money(
                Account.objects.get(pk=self.alice.pk), Decimal("600.00")
            )
        )
        print(f"\n  withdraw outcomes: {results}")

        self.alice.refresh_from_db()
        self.assertGreaterEqual(self.alice.balance, Decimal("0.00"))

        # Exactly one withdrawal may succeed, and the balance must agree
        # with the number of WITHDRAW rows in the ledger.
        withdrawals = self.alice.transactions.filter(
            transaction_type=Transaction.WITHDRAW
        ).count()
        self.assertEqual(
            self.alice.balance,
            WELCOME_BONUS - (Decimal("600.00") * withdrawals),
        )
        self.assertLessEqual(withdrawals, 1)


class ConcurrentTransferTests(TransactionTestCase):
    def setUp(self):
        self.alice = get_or_create_account(User.objects.create_user("alice"))
        self.bob = get_or_create_account(User.objects.create_user("bob"))

    def test_two_simultaneous_transfers_cannot_overdraw(self):
        """$1000 in the account, two threads each try to send $600 to bob."""
        results = run_together(
            lambda: transfer_money(
                Account.objects.get(pk=self.alice.pk),
                Account.objects.get(pk=self.bob.pk),
                Decimal("600.00"),
            )
        )
        print(f"\n  transfer outcomes: {results}")

        self.alice.refresh_from_db()
        self.bob.refresh_from_db()

        self.assertGreaterEqual(self.alice.balance, Decimal("0.00"))
        # Whatever happened, the bank must still hold exactly $2000.
        self.assertEqual(self.alice.balance + self.bob.balance, WELCOME_BONUS * 2)

    def test_opposite_transfers_do_not_deadlock(self):
        """alice pays bob while bob pays alice -- the classic deadlock."""
        alice_pk, bob_pk = self.alice.pk, self.bob.pk
        barrier = threading.Barrier(2)
        results = []

        def pay(from_pk, to_pk):
            def work():
                barrier.wait()
                try:
                    transfer_money(
                        Account.objects.get(pk=from_pk),
                        Account.objects.get(pk=to_pk),
                        Decimal("100.00"),
                    )
                    results.append("ok")
                except Exception as exc:
                    results.append(f"{type(exc).__name__}: {exc}")
                finally:
                    connections.close_all()

            return work

        threads = [
            threading.Thread(target=pay(alice_pk, bob_pk)),
            threading.Thread(target=pay(bob_pk, alice_pk)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        print(f"\n  opposite-transfer outcomes: {results}")
        for thread in threads:
            self.assertFalse(thread.is_alive(), "deadlocked: thread never finished")

        self.alice.refresh_from_db()
        self.bob.refresh_from_db()
        self.assertEqual(self.alice.balance + self.bob.balance, WELCOME_BONUS * 2)
