"""Lessons 11-14: the DVP settlement story, end to end, through the real HTTP API.

Needs a FRESHLY SEEDED database and a running API:

    npm run db:reset           deletes ALL local data, then migrates and seeds
    npm run dev                the API on :8000 (and the UI on :5173)
    npm run demo:settlement    the same as: uv run --directory server python -m demo.settlement_scenario

    npm run demo:settlement -- --base-url http://127.0.0.1:8001     (another server)

It signs in as alpha, beta, gamma, operator and auditor (password demo-password-1), runs the seven
steps of the settlement story in order, and checks every HTTP status, recycle reason, net balance,
collateral monitor, end-of-day figure and NSS line. Each step prints a few short lines (and the
settlement accounts whenever they changed), so the output doubles as a script for rehearsing the demo.

It is a black box: it sends only requests a user could send from the UI and never reads the database.
Every expected number starts from the seed, so before step 1 it refuses to run unless the database
looks freshly seeded (no deliver order, payment, mint, transfer or closed day yet).

Exit codes: 0 every step matched; 1 the first mismatch (what was expected, what came back);
2 it could not start (API unreachable, a login refused, or the database not freshly seeded).

Simulation only. Dollar figures are DTC-shaped but scaled down 1:100,000 and illustrative.
"""

import argparse
import sys
import uuid
from datetime import date, timedelta
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
PASSWORD = "demo-password-1"  # the public demo password (seeds/002_users.sql)
RESET_HINT = (
    "Start from a fresh seed: `npm run db:reset` (it DELETES all local data), keep the API running "
    "(`npm run dev`), then run this again."
)

# ---------- The seed (server/db/seeds/001-004): units, and money in whole cents ----------

NAMES = {101: "alpha", 102: "beta", 103: "gamma", 104: "Delta"}
LOGINS = {  # username -> (role, account) as POST /api/login must answer
    "alpha": ("PARTICIPANT", 101),
    "beta": ("PARTICIPANT", 102),
    "gamma": ("PARTICIPANT", 103),
    "operator": ("OPERATOR", None),
    "auditor": ("AUDITOR", None),
}
PRIOR_CLOSE = 10_000  # $100.00
HAIRCUT_BPS = 500  # 5%
COLLATERAL_PER_UNIT = 9_500  # $100.00 less the 5% haircut = $95.00 per TRADITIONAL unit (tokenized: $0)
MAX_NET_DEBIT_CAP = 3_844_000  # DTC's $3.844B maximum, scaled 1:100,000
NET_DEBIT_CAPS = {101: 3_844_000, 102: 1_500_000, 103: 2_500_000, 104: 0}
SETTLING_BANK = {101: 201, 102: 201, 103: 202, 104: 202}
TOTAL_UNITS = 3_000
MINT_REFUSED = "collateral monitor would go negative: tokenized units have no collateral value"

# Every settlement account after each step: account -> (traditional units, tokenized units, net cents).
# Collateral, the collateral monitor, the cap and the headroom all follow from these (see account_row).
Book = dict[int, tuple[int, int, int]]
SEEDED: Book = {101: (1000, 0, 0), 102: (1000, 0, 0), 103: (1000, 0, 0), 104: (0, 0, 0)}
AFTER_STEP_2: Book = {**SEEDED, 101: (800, 0, 2_000_000), 102: (1100, 0, -1_000_000), 103: (1100, 0, -1_000_000)}
AFTER_STEP_3: Book = {**AFTER_STEP_2, 103: (106, 994, -1_000_000)}
AFTER_STEP_5: Book = {**AFTER_STEP_3, 101: (700, 0, 3_000_000), 103: (206, 994, -1_950_000)}
AFTER_STEP_6: Book = {account: (trad, tok, 0) for account, (trad, tok, _net) in AFTER_STEP_5.items()}

# Final settlement: each account's net just before it is settled to zero, and one net-net line per bank
# (Bank One = alpha + beta, Bank Two = gamma + Delta). The lines sum to +$500.00 = gamma's payment.
FINAL_FIGURES = [(101, 3_000_000), (102, -1_000_000), (103, -1_950_000), (104, 0)]
NSS_FILE = [(201, "Settling Bank One (demo)", 2_000_000), (202, "Settling Bank Two (demo)", -1_950_000)]
PAYMENT = 50_000  # gamma's settlement progress payment, $500.00

# The audit trail after the story, oldest first: (type, actor, source, destination, quantity, amount_cents).
# A DELIVER_ORDER is the request; its DVP_SETTLEMENT is the event that moved units and money, written by
# the system (no actor) inside whichever request let it settle.
OPENINGS = [("OPENING", None, None, account, 1000, None) for account in (101, 102, 103)]
TRAIL = [
    ("DELIVER_ORDER", "alpha", 101, 102, 200, 2_000_000),  # step 1: #1 recycles
    ("DELIVER_ORDER", "beta", 102, 103, 100, 1_000_000),  # step 2: #2 ...
    ("DVP_SETTLEMENT", None, 102, 103, 100, 1_000_000),  # ... settles,
    ("DVP_SETTLEMENT", None, 101, 102, 200, 2_000_000),  # and #1 settles in beta's request
    ("CONVERT_TO_TOKENIZED", "gamma", 103, None, 994, None),  # step 3: the refused 995 left no row
    ("DELIVER_ORDER", "alpha", 101, 103, 100, 1_000_000),  # step 4: #3 recycles
    ("DELIVER_ORDER", "alpha", 101, 102, 100, 1_000_000),  # #4 recycles
    ("SETTLEMENT_PROGRESS_PAYMENT", "gamma", None, 103, None, PAYMENT),  # step 5
    ("DVP_SETTLEMENT", None, 101, 103, 100, 1_000_000),  # #3 settles in gamma's payment request
    ("VALUED_CUTOFF", "operator", None, None, None, None),  # step 6: #4 drops
    ("FINAL_SETTLEMENT", "operator", None, None, None, None),
]
RECONCILIATION_CHECKS = (
    "conservation",
    "positionsVsLedger",
    "cashLinesNetToZero",
    "settlementVsCashLedger",
    "collateralVsPositions",
    "settledInstructionsVsLedgers",
    "riskControls",
    "closedDays",
)


def account_row(book: Book, account_id: int) -> dict[str, int]:
    """What GET /api/settlement/accounts must show for one account in `book`."""
    traditional, tokenized, net = book[account_id]
    collateral = traditional * COLLATERAL_PER_UNIT  # tokenized units count zero
    cap = NET_DEBIT_CAPS[account_id]
    return {
        "settling_bank_id": SETTLING_BANK[account_id],
        "traditional_units": traditional,
        "tokenized_units": tokenized,
        "net_cents": net,
        "collateral_value_cents": collateral,
        "collateral_monitor_cents": collateral + net,  # CM = collateral value + net settlement balance
        "net_debit_cap_cents": cap,
        "headroom_cents": max(0, min(collateral + net, cap + net)),  # the largest debit it can take now
    }


def account_summary(book: Book, account_id: int) -> dict[str, int]:
    """The "account" object of a deliver order or payment result: the caller's state afterwards."""
    row = account_row(book, account_id)
    return {
        "netCents": row["net_cents"],
        "collateralValueCents": row["collateral_value_cents"],
        "collateralMonitorCents": row["collateral_monitor_cents"],
        "netDebitCapCents": row["net_debit_cap_cents"],
        "traditionalUnits": row["traditional_units"],
    }


def next_business_day(day: date) -> date:
    """The weekday after `day` (Friday, Saturday and Sunday -> Monday). No holiday calendar, like the app."""
    day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def usd(cents: int, *, sign: bool = False) -> str:
    """12345678 -> "$123,456.78"; a debit "-$..."; with sign=True a credit is "+$..."."""
    text = f"${abs(cents) // 100:,}.{abs(cents) % 100:02d}"
    if cents < 0:
        return "-" + text
    return "+" + text if sign and cents > 0 else text


def say(text: str = "") -> None:
    print(f"  {text}" if text else "")


def print_accounts(rows: list[dict[str, Any]]) -> None:
    """The settlement accounts as a small table (rows from GET /api/settlement/accounts)."""
    print(f"    {'account':<10}{'trad':>7}{'tok':>6}{'net':>15}{'collateral':>14}{'CM':>14}{'NDC cap':>13}"
          f"{'headroom':>13}")
    for row in rows:
        print(f"    {row['account_id']} {NAMES[row['account_id']]:<6}{row['traditional_units']:>7,}"
              f"{row['tokenized_units']:>6,}{usd(row['net_cents'], sign=True):>15}"
              f"{usd(row['collateral_value_cents']):>14}{usd(row['collateral_monitor_cents']):>14}"
              f"{usd(row['net_debit_cap_cents']):>13}{usd(row['headroom_cents']):>13}")


class Mismatch(Exception):
    """A response differs from the story: exit 1."""


class CannotStart(Exception):
    """The story cannot run here (API down, login refused, database not freshly seeded): exit 2."""


_ABSENT = "<absent>"


def expect(what: str, actual: Any, expected: Any) -> None:
    """Raise Mismatch unless actual == expected. For two dicts, name only the fields that differ."""
    if actual == expected:
        return
    if isinstance(actual, dict) and isinstance(expected, dict):
        keys = [*expected, *(key for key in actual if key not in expected)]
        details = [f"    {key}: expected {expected.get(key, _ABSENT)!r}, got {actual.get(key, _ABSENT)!r}"
                   for key in keys if actual.get(key, _ABSENT) != expected.get(key, _ABSENT)]
        raise Mismatch(f"{what}\n" + "\n".join(details))
    raise Mismatch(f"{what}\n    expected: {expected!r}\n    got:      {actual!r}")


class Session:
    """One signed-in user: an httpx client with its own cookie jar, like one browser."""

    last_response: httpx.Response | None = None  # shared by every session: shown when a check fails

    def __init__(self, base_url: str, username: str) -> None:
        self.username = username
        self.client = httpx.Client(base_url=base_url, timeout=30)

    def request(self, method: str, path: str, body: dict[str, Any] | None, status: int) -> Any:
        response = self.client.request(method, path, json=body)
        Session.last_response = response
        if response.status_code != status:
            raise Mismatch(f"{self.username} {method} {path}: HTTP {response.status_code}, expected {status}")
        return response.json()

    def get(self, path: str) -> Any:
        return self.request("GET", path, None, 200)

    def post(self, path: str, body: dict[str, Any], status: int) -> Any:
        return self.request("POST", path, body, status)

    def close(self) -> None:
        self.client.close()


class Scenario:
    """The seven steps, in order. Each one acts, checks every number, and prints what happened."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.run_id = uuid.uuid4().hex[:8]  # fresh idempotency keys on every run
        self.users: dict[str, Session] = {}
        self.orders: dict[str, dict[str, int]] = {}  # "#1" -> instruction id, from, to, quantity, amount
        self.business_date = ""
        self.step = "the start checks"
        self.started = False  # True once the fresh-seed check passed

    def run(self) -> None:
        self.start()
        for step in (self.step_1, self.step_2, self.step_3, self.step_4, self.step_5, self.step_6, self.step_7):
            step()

    def close(self) -> None:
        for session in self.users.values():
            session.close()

    # ---------- helpers ----------

    def begin(self, number: int, title: str) -> None:
        self.step = f"step {number} ({title})"
        print(f"\nStep {number}. {title}")

    def key(self, label: str) -> str:
        return f"demo-{self.run_id}-{label}"

    def order_id(self, label: str) -> int:
        return self.orders[label]["id"]

    def label_of(self, instruction_id: int) -> str:
        for label, order in self.orders.items():
            if order["id"] == instruction_id:
                return label
        raise Mismatch(f"delivery instruction {instruction_id} is not one of this story's orders")

    def deliver(self, username: str, label: str, *, to: int, quantity: int, amount: int, status: int) -> dict:
        """POST /api/delivery-orders as `username` and remember the new instruction under `label`."""
        body = self.users[username].post(
            "/api/delivery-orders",
            {"toAccountId": to, "quantity": quantity, "amountCents": amount, "idempotencyKey": self.key(label)},
            status,
        )
        for field in ("operationId", "instructionId"):
            expect(f"type of {field}", type(body[field]), int)
        known = [order["id"] for order in self.orders.values()]
        expect(f"{label} gets a new instruction id", body["instructionId"] in known, False)
        self.orders[label] = {"id": body["instructionId"], "from": LOGINS[username][1], "to": to,
                              "quantity": quantity, "amount": amount}
        return body

    def expect_order_result(self, body: dict, label: str, *, status: str, reason: str | None,
                            released: list[str], book: Book) -> None:
        """The whole deliver order result, field by field (the deliverer's view: reasons masked)."""
        order = self.orders[label]
        settlement_id = body.get("settlementOperationId")
        if status == "SETTLED":
            expect("type of settlementOperationId of a settled order", type(settlement_id), int)
        expect(f"the deliver order {label} result", body, {
            "operationId": body["operationId"],
            "type": "DELIVER_ORDER",
            "instructionId": order["id"],
            "businessDate": self.business_date,
            "fromAccountId": order["from"],
            "toAccountId": order["to"],
            "quantity": order["quantity"],
            "amountCents": order["amount"],
            "status": status,
            "recycleReason": reason,
            "settlementOperationId": settlement_id if status == "SETTLED" else None,
            "releasedInstructionIds": [self.order_id(r) for r in released],
            "account": account_summary(book, order["from"]),
        })

    def check_accounts(self, book: Book, *, show: bool) -> None:
        """Every settlement account, as the auditor sees it, must match `book` exactly."""
        rows = self.users["auditor"].get("/api/settlement/accounts")
        expect("settlement account ids", [row["account_id"] for row in rows], sorted(book))
        for row in rows:
            account = row["account_id"]
            expected = account_row(book, account)
            expect(f"settlement account {account} ({NAMES[account]})",
                   {field: row[field] for field in expected}, expected)
        if show:
            print_accounts(rows)
        else:
            say("settlement accounts unchanged (checked)")

    def check_instructions(self, viewer: str, expected: list[tuple[str, str, str | None]]) -> None:
        """GET /api/delivery-orders as `viewer`: (order, status, recycle reason) per row, oldest first.

        The auditor sees every order with its exact reason. A participant sees only orders it delivers
        or receives, and the other side's reason only as WAITING_ON_COUNTERPARTY (a lab choice).
        """
        rows = self.users[viewer].get("/api/delivery-orders")
        ids = [row["id"] for row in rows]
        expect(f"{viewer}'s delivery instructions come newest first", ids, sorted(ids, reverse=True))
        actual = []
        for row in reversed(rows):
            label = self.label_of(row["id"])
            order = self.orders[label]
            expect(f"instruction {label} as {viewer} sees it (date, from, to, quantity, amount)",
                   (row["business_date"], row["deliverer_account_id"], row["receiver_account_id"],
                    row["quantity"], row["amount_cents"]),
                   (self.business_date, order["from"], order["to"], order["quantity"], order["amount"]))
            expect(f"instruction {label} is settled exactly when it has a settlement operation",
                   row["settlement_operation_id"] is not None, row["status"] == "SETTLED")
            actual.append((label, row["status"], row["recycle_reason"]))
        expect(f"{viewer}'s delivery instructions (order, status, reason)", actual, expected)

    def check_day(self, business_date: str, phase: str) -> None:
        day = self.users["operator"].get("/api/settlement/day")
        expect("the settlement day (date, phase)", (day["business_date"], day["phase"]),
               (business_date, phase))

    # ---------- before step 1 ----------

    def start(self) -> None:
        """Health, the five logins and the fresh-seed check. Any problem here is CannotStart (exit 2)."""
        anonymous = Session(self.base_url, "anonymous")
        try:
            health = anonymous.get("/api/health")
        except httpx.TransportError as err:
            raise CannotStart(f"cannot reach the API at {self.base_url} ({err}). Start it with `npm run dev`, "
                              "or pass --base-url.") from None
        except Mismatch as err:
            raise CannotStart(f"the API is not healthy ({err}): is the database running? (`npm run db:up`)") from None
        finally:
            anonymous.close()
        if health != {"status": "ok", "database": "ok"}:
            raise CannotStart(f"GET /api/health answered {health}")

        for username, (role, account_id) in LOGINS.items():
            session = self.users[username] = Session(self.base_url, username)
            try:
                user = session.post("/api/login", {"username": username, "password": PASSWORD}, 200)
            except Mismatch as err:
                raise CannotStart(f"{err}: is the database seeded? {RESET_HINT}") from None
            if (user["role"], user["account_id"]) != (role, account_id):
                raise CannotStart(f"{username} signed in as {user}, expected role {role}, account {account_id}")

        try:
            self.check_freshly_seeded()
        except Mismatch as err:
            raise CannotStart(f"the fresh-seed check could not read the API: {err}") from None
        self.started = True

        print(f"DVP settlement story against {self.base_url}: business date {self.business_date}, OPEN")
        say("Signed in: alpha (101), beta (102), gamma (103), operator, auditor. The database is freshly seeded.")
        say("DEMO-TREASURY as collateral: $100.00 prior close less a 5% haircut = $95.00 per traditional unit;")
        say("tokenized units count $0. Illustrative dollar figures, scaled 1:100,000 (the $38,440.00 maximum")
        say("net debit cap stands for DTC's $3.844B). CM = collateral + net, must stay >= $0; net >= -cap.")
        self.check_accounts(SEEDED, show=True)

    def check_freshly_seeded(self) -> None:
        """Refuse to run (CannotStart) unless the database is exactly as the seed left it."""
        auditor = self.users["auditor"]
        problems: list[str] = []

        day = auditor.get("/api/settlement/day")
        if day["phase"] != "OPEN":
            problems.append(f"the settlement day {day['business_date']} is {day['phase']}, not OPEN")
        reference = (day["prior_close_cents"], day["haircut_bps"], day["collateral_cents_per_unit"],
                     day["max_net_debit_cap_cents"])
        seeded_reference = (PRIOR_CLOSE, HAIRCUT_BPS, COLLATERAL_PER_UNIT, MAX_NET_DEBIT_CAP)
        if reference != seeded_reference:
            problems.append(f"(prior close, haircut, collateral per unit, maximum cap) is {reference}, "
                            f"the seed has {seeded_reference}")

        positions = {row["account_id"]: (row["traditional_units"], row["tokenized_units"])
                     for row in auditor.get("/api/holdings")}
        seeded_positions = {account: (trad, tok) for account, (trad, tok, _net) in SEEDED.items()}
        if positions != seeded_positions:
            problems.append(f"positions (traditional, tokenized) are {positions}, the seed has {seeded_positions}")

        accounts = auditor.get("/api/settlement/accounts")
        if sorted(row["account_id"] for row in accounts) != sorted(SEEDED):
            problems.append(f"settlement accounts {[row['account_id'] for row in accounts]}, expected {sorted(SEEDED)}")
        for row in accounts:
            if row["account_id"] not in SEEDED:
                continue
            expected = account_row(SEEDED, row["account_id"])
            differences = [f"{field} {row.get(field)} (seed {value})"
                           for field, value in expected.items() if row.get(field) != value]
            if differences:
                problems.append(f"settlement account {row['account_id']}: {', '.join(differences)}")

        instructions = auditor.get("/api/delivery-orders")
        if instructions:
            problems.append(f"{len(instructions)} delivery instruction(s) already exist")
        closed_days = auditor.get("/api/settlement/closed-days")
        if closed_days:
            problems.append(f"{len(closed_days)} settlement day(s) already closed: settlement figures exist")
        later = [row["operation_type"] for row in auditor.get("/api/operations") if row["operation_type"] != "OPENING"]
        if later:
            problems.append(f"{len(later)} operation(s) already ran after the opening balances "
                            f"(newest: {', '.join(later[:3])})")

        if problems:
            raise CannotStart("the database is not freshly seeded:\n  - " + "\n  - ".join(problems) + "\n" + RESET_HINT)
        self.business_date = day["business_date"]

    # ---------- the story (spec section 1) ----------

    def step_1(self) -> None:
        self.begin(1, "alpha: deliver order #1, 200 units to beta against $20,000.00")
        body = self.deliver("alpha", "#1", to=102, quantity=200, amount=2_000_000, status=202)
        self.expect_order_result(body, "#1", status="RECYCLING", reason="WAITING_ON_COUNTERPARTY", released=[],
                                 book=SEEDED)
        self.check_instructions("auditor", [("#1", "RECYCLING", "RECEIVER_NET_DEBIT_CAP")])
        self.check_instructions("beta", [("#1", "RECYCLING", "RECEIVER_NET_DEBIT_CAP")])
        self.check_instructions("alpha", [("#1", "RECYCLING", "WAITING_ON_COUNTERPARTY")])
        self.check_instructions("gamma", [])
        say(f"-> 202 RECYCLING, instruction {self.order_id('#1')}: reason RECEIVER_NET_DEBIT_CAP")
        say("   beta's net would be -$20,000.00, beyond its $15,000.00 net debit cap. Nothing moved.")
        say("   alpha (the deliverer) sees only WAITING_ON_COUNTERPARTY; beta, the receiver, sees the reason.")
        self.check_accounts(SEEDED, show=False)

    def step_2(self) -> None:
        self.begin(2, "beta: deliver order #2, 100 units to gamma against $10,000.00")
        body = self.deliver("beta", "#2", to=103, quantity=100, amount=1_000_000, status=201)
        self.expect_order_result(body, "#2", status="SETTLED", reason=None, released=["#1"], book=AFTER_STEP_2)
        self.check_instructions("auditor", [("#1", "SETTLED", None), ("#2", "SETTLED", None)])
        say("-> 201 SETTLED. Beta's +$10,000.00 credit lets #1 settle in the same request:")
        say(f"   releasedInstructionIds {body['releasedInstructionIds']} (beta sees #1 because it is #1's receiver).")
        say("   Beta's net: +$10,000.00 - $20,000.00 = -$10,000.00, inside its $15,000.00 cap.")
        self.check_accounts(AFTER_STEP_2, show=True)

    def step_3(self) -> None:
        self.begin(3, "gamma: convert 995, then 994, traditional units to tokenized (mint)")
        refused = self.users["gamma"].post(
            "/api/conversions", {"direction": "TO_TOKENIZED", "quantity": 995, "idempotencyKey": self.key("mint-995")},
            422,
        )
        expect("the refused mint", refused, {"error": MINT_REFUSED})
        say(f"-> 995: 422 {MINT_REFUSED}")
        say("   CM would be 105 x $95.00 - $10,000.00 = -$25.00 (tokenized units are worth $0 as collateral).")
        self.check_accounts(AFTER_STEP_2, show=False)

        minted = self.users["gamma"].post(
            "/api/conversions", {"direction": "TO_TOKENIZED", "quantity": 994, "idempotencyKey": self.key("mint-994")},
            201,
        )
        expect("type of operationId", type(minted["operationId"]), int)
        expect("the mint of 994", minted, {
            "operationId": minted["operationId"], "type": "CONVERT_TO_TOKENIZED", "accountId": 103,
            "quantity": 994, "traditionalUnits": 106, "tokenizedUnits": 994,
        })
        own = self.users["gamma"].get("/api/settlement/accounts")  # a participant sees only its own row
        expect("gamma's own settlement view (account, CM, headroom)",
               [(row["account_id"], row["collateral_monitor_cents"], row["headroom_cents"]) for row in own],
               [(103, 7_000, 7_000)])
        say("-> 994: 201. CM = 106 x $95.00 - $10,000.00 = $70.00 (gamma's own view shows only its account).")
        self.check_accounts(AFTER_STEP_3, show=True)

    def step_4(self) -> None:
        self.begin(4, "alpha: deliver orders #3 (100 units to gamma) and #4 (100 units to beta), $10,000.00 each")
        body = self.deliver("alpha", "#3", to=103, quantity=100, amount=1_000_000, status=202)
        self.expect_order_result(body, "#3", status="RECYCLING", reason="WAITING_ON_COUNTERPARTY", released=[],
                                 book=AFTER_STEP_3)
        body = self.deliver("alpha", "#4", to=102, quantity=100, amount=1_000_000, status=202)
        self.expect_order_result(body, "#4", status="RECYCLING", reason="WAITING_ON_COUNTERPARTY", released=[],
                                 book=AFTER_STEP_3)
        self.check_instructions("auditor", [
            ("#1", "SETTLED", None), ("#2", "SETTLED", None),
            ("#3", "RECYCLING", "RECEIVER_COLLATERAL_MONITOR"), ("#4", "RECYCLING", "RECEIVER_NET_DEBIT_CAP"),
        ])
        self.check_instructions("alpha", [
            ("#1", "SETTLED", None), ("#3", "RECYCLING", "WAITING_ON_COUNTERPARTY"),
            ("#4", "RECYCLING", "WAITING_ON_COUNTERPARTY"),
        ])
        self.check_instructions("gamma", [("#2", "SETTLED", None), ("#3", "RECYCLING", "RECEIVER_COLLATERAL_MONITOR")])
        self.check_instructions("beta", [
            ("#1", "SETTLED", None), ("#2", "SETTLED", None), ("#4", "RECYCLING", "RECEIVER_NET_DEBIT_CAP"),
        ])
        say(f"-> #3 (instruction {self.order_id('#3')}): 202 RECYCLING, RECEIVER_COLLATERAL_MONITOR")
        say("   gamma's CM would be 206 x $95.00 - $20,000.00 = -$430.00.")
        say(f"-> #4 (instruction {self.order_id('#4')}): 202 RECYCLING, RECEIVER_NET_DEBIT_CAP")
        say("   beta's net would be -$20,000.00, beyond its $15,000.00 cap.")
        say("   alpha sees WAITING_ON_COUNTERPARTY on both; gamma never sees #4 (it is not a party to it).")
        self.check_accounts(AFTER_STEP_3, show=False)

    def step_5(self) -> None:
        self.begin(5, "gamma: settlement progress payment of $500.00 (a simulated Fed funds wire)")
        body = self.users["gamma"].post(
            "/api/settlement-progress-payments", {"amountCents": PAYMENT, "idempotencyKey": self.key("payment")}, 201,
        )
        expect("type of operationId", type(body["operationId"]), int)
        expect("the payment result", body, {
            "operationId": body["operationId"], "type": "SETTLEMENT_PROGRESS_PAYMENT", "accountId": 103,
            "amountCents": PAYMENT, "businessDate": self.business_date,
            "releasedInstructionIds": [self.order_id("#3")], "account": account_summary(AFTER_STEP_5, 103),
        })
        self.check_instructions("auditor", [
            ("#1", "SETTLED", None), ("#2", "SETTLED", None),
            ("#3", "SETTLED", None), ("#4", "RECYCLING", "RECEIVER_NET_DEBIT_CAP"),
        ])
        say(f"-> 201. Gamma's net -$10,000.00 -> -$9,500.00; the retry settles #3: releasedInstructionIds "
            f"{body['releasedInstructionIds']}")
        say("   gamma's CM after #3 = 206 x $95.00 - $19,500.00 = $70.00. #4 still recycling (beta's cap).")
        self.check_accounts(AFTER_STEP_5, show=True)

    def step_6(self) -> None:
        self.begin(6, "operator: valued cutoff, then final settlement")
        operator = self.users["operator"]
        cutoff = operator.post("/api/settlement-day/valued-cutoff", {"idempotencyKey": self.key("cutoff")}, 201)
        expect("type of operationId", type(cutoff["operationId"]), int)
        expect("the valued cutoff result", cutoff, {
            "operationId": cutoff["operationId"], "type": "VALUED_CUTOFF", "businessDate": self.business_date,
            "droppedInstructionIds": [self.order_id("#4")],
        })
        self.check_instructions("auditor", [
            ("#1", "SETTLED", None), ("#2", "SETTLED", None),
            ("#3", "SETTLED", None), ("#4", "DROPPED_AT_CUTOFF", "RECEIVER_NET_DEBIT_CAP"),
        ])
        self.check_day(self.business_date, "VALUED_CUTOFF_PASSED")
        say(f"-> valued cutoff 201: #4 DROPPED_AT_CUTOFF (it keeps its reason); droppedInstructionIds "
            f"{cutoff['droppedInstructionIds']}")

        next_date = next_business_day(date.fromisoformat(self.business_date)).isoformat()
        final = operator.post("/api/settlement-day/final-settlement", {"idempotencyKey": self.key("final")}, 201)
        expect("type of operationId", type(final["operationId"]), int)
        expect("the final settlement result", final, {
            "operationId": final["operationId"], "type": "FINAL_SETTLEMENT", "businessDate": self.business_date,
            "nextBusinessDate": next_date,
            "figures": [{"accountId": account, "finalNetCents": net} for account, net in FINAL_FIGURES],
            "nssFile": [{"settlingBankId": bank, "bankName": name, "netNetCents": net} for bank, name, net in NSS_FILE],
        })
        say("-> final settlement 201. Figures, each account's final net before it is settled to zero:")
        say("     " + ", ".join(f"{NAMES[account]} {usd(net, sign=True)}" for account, net in FINAL_FIGURES))
        say("   NSS file, one net-net line per settling bank (+ is paid, - pays):")
        for bank, name, net in NSS_FILE:
            say(f"     {bank} {name:<26}{usd(net, sign=True):>13}")
        total = sum(net for _bank, _name, net in NSS_FILE)
        expect("the NSS lines sum to the progress payment", total, PAYMENT)
        say(f"   The lines sum to {usd(total, sign=True)} = gamma's progress payment, already received today.")

        closed = operator.get("/api/settlement/closed-days")
        expect("closed days", [day["business_date"] for day in closed], [self.business_date])
        expect("the closed day's figures (by account)",
               sorted((row["account_id"], row["final_net_cents"]) for row in closed[0]["figures"]), FINAL_FIGURES)
        expect("the closed day's NSS file (by bank)",
               sorted((row["settling_bank_id"], row["bank_name"], row["net_net_cents"])
                      for row in closed[0]["nss_file"]),
               NSS_FILE)
        self.check_day(next_date, "OPEN")
        say(f"   Every net is now $0.00; the next business day, {next_date}, is OPEN.")
        self.check_accounts(AFTER_STEP_6, show=True)

    def step_7(self) -> None:
        self.begin(7, "auditor: reconciliation, holdings and the audit trail")
        auditor = self.users["auditor"]
        report = auditor.get("/api/reconciliation")
        expect("reconciliation passed", report["passed"], True)
        for check in RECONCILIATION_CHECKS:
            rows = report[check]
            expect(f"{check} has rows", bool(rows), True)
            expect(f"{check}: every row ok", [row["ok"] for row in rows], [True] * len(rows))
        expect("conservation", report["conservation"], [{
            "symbol": "DEMO-TREASURY", "total_custodied_units": TOTAL_UNITS, "traditional_total": 2006,
            "tokenized_total": 994, "held_total": TOTAL_UNITS, "ok": True,
        }])
        final_positions = {101: (700, 0), 102: (1100, 0), 103: (206, 994), 104: (0, 0)}
        expect("positions vs ledger (stored = ledger)",
               {(row["account_id"], row["representation"]): (row["stored_units"], row["ledger_units"])
                for row in report["positionsVsLedger"]},
               {key: (units, units) for account, (trad, tok) in final_positions.items()
                for key, units in (((account, "TRADITIONAL"), trad), ((account, "TOKENIZED"), tok))})
        expect("settlement balance vs cash ledger (stored, ledger)",
               {row["account_id"]: (row["stored_net_cents"], row["ledger_net_cents"])
                for row in report["settlementVsCashLedger"]},
               {account: (0, 0) for account in SEEDED})
        expect("collateral vs positions (traditional, tokenized, per unit, stored, expected)",
               {row["account_id"]: (row["traditional_units"], row["tokenized_units"],
                                    row["collateral_cents_per_unit"], row["stored_collateral_cents"],
                                    row["expected_collateral_cents"])
                for row in report["collateralVsPositions"]},
               {account: (trad, tok, COLLATERAL_PER_UNIT, trad * COLLATERAL_PER_UNIT, trad * COLLATERAL_PER_UNIT)
                for account, (trad, tok) in final_positions.items()})
        expect("settled instructions vs ledgers (failing, orphan settlements)",
               [(row["failing"], row["orphan_settlements"]) for row in report["settledInstructionsVsLedgers"]],
               [(0, 0)])
        expect("cash lines net to zero (failing)",
               [row["failing"] for row in report["cashLinesNetToZero"]], [0])
        expect("risk controls (net, cap, expected cap, CM)",
               {row["account_id"]: (row["net_cents"], row["net_debit_cap_cents"], row["expected_cap_cents"],
                                    row["collateral_monitor_cents"]) for row in report["riskControls"]},
               {account: (0, NET_DEBIT_CAPS[account], NET_DEBIT_CAPS[account], trad * COLLATERAL_PER_UNIT)
                for account, (trad, _tok) in final_positions.items()})
        expect("closed days (date, figures, NSS, payments, Fed lines, accounts and banks mismatched)",
               [(row["business_date"], row["figures_total_cents"], row["nss_total_cents"],
                 row["payments_total_cents"], row["fed_total_cents"], row["accounts_mismatched"],
                 row["banks_mismatched"]) for row in report["closedDays"]],
               [(self.business_date, PAYMENT, PAYMENT, PAYMENT, 0, 0, 0)])
        say(f"-> passed: {len(RECONCILIATION_CHECKS)} checks, every row ok in one snapshot.")
        say(f"   Closed day {self.business_date}: figures = NSS file = payments = +$500.00; "
            "DTC's Fed lines net to $0.00;")
        say("   each bank's NSS line and Fed line = the sum of its accounts' figures.")

        holdings = {row["account_id"]: (row["traditional_units"], row["tokenized_units"])
                    for row in auditor.get("/api/holdings")}
        expect("holdings (traditional, tokenized)", holdings, final_positions)
        expect("total units", sum(trad + tok for trad, tok in holdings.values()), TOTAL_UNITS)
        say("-> units still 3,000: alpha 700, beta 1,100, gamma 206 + 994 tokenized, Delta 0.")

        operations = sorted(auditor.get("/api/operations"), key=lambda row: row["id"])
        trail = [(row["operation_type"], row["actor_username"], row["source_account_id"],
                  row["destination_account_id"], row["quantity"], row["amount_cents"]) for row in operations]
        expect("the audit trail, oldest first", trail, OPENINGS + TRAIL)
        self.check_settlement_links(operations)
        say("-> audit trail after the 3 opening balances (GET /api/operations, oldest first):")
        for kind, actor, source, destination, quantity, amount in TRAIL:
            accounts = " -> ".join(str(account) for account in (source, destination) if account is not None)
            units = f"{quantity} units" if quantity is not None else ""
            money = usd(amount) if amount is not None else ""
            say(f"     {kind:<28}{actor or '(system)':<10}{accounts:<12}{units:<11}{money:>11}".rstrip())

    def check_settlement_links(self, operations: list[dict[str, Any]]) -> None:
        """Each order points at its own DELIVER_ORDER request and (when settled) its own DVP_SETTLEMENT event."""
        by_id = {row["id"]: row for row in operations}
        for row in self.users["auditor"].get("/api/delivery-orders"):
            label = self.label_of(row["id"])
            order = self.orders[label]
            shape = (order["from"], order["to"], order["quantity"], order["amount"])
            links = [("DELIVER_ORDER", row["submitted_operation_id"])]
            if row["settlement_operation_id"] is not None:
                links.append(("DVP_SETTLEMENT", row["settlement_operation_id"]))
            for kind, operation_id in links:
                operation = by_id.get(operation_id, {})
                expect(f"{label}'s {kind} operation {operation_id} (type, from, to, quantity, amount)",
                       (operation.get("operation_type"), operation.get("source_account_id"),
                        operation.get("destination_account_id"), operation.get("quantity"),
                        operation.get("amount_cents")),
                       (kind, *shape))


def report_mismatch(scenario: Scenario, message: str) -> None:
    sys.stdout.flush()
    print(f"\nMISMATCH in {scenario.step}:\n  {message}", file=sys.stderr)
    response = Session.last_response
    if response is not None:
        print(f"  last response: HTTP {response.status_code} for {response.request.method} {response.request.url.path}"
              f"\n  {response.text[:1500]}", file=sys.stderr)
    print("Stopped at the first mismatch; the later steps did not run. The database now holds a partial story:"
          f"\n{RESET_HINT}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the DVP settlement story against a freshly seeded API and check every number."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"the API to drive (default {DEFAULT_BASE_URL})")
    args = parser.parse_args(argv)
    scenario = Scenario(args.base_url.rstrip("/"))
    try:
        scenario.run()
    except CannotStart as err:
        sys.stdout.flush()
        print(f"\nCANNOT START: {err}", file=sys.stderr)
        return 2
    except Mismatch as err:
        report_mismatch(scenario, str(err))
        return 1
    except (KeyError, TypeError, IndexError, ValueError) as err:
        # A missing field, a null where a list was due, a body that is not JSON...
        message = f"a response did not have the documented shape ({type(err).__name__}: {err})"
        if not scenario.started:
            sys.stdout.flush()
            print(f"\nCANNOT START: {message}", file=sys.stderr)
            return 2
        report_mismatch(scenario, message)
        return 1
    except httpx.TransportError as err:
        if not scenario.started:
            sys.stdout.flush()
            print(f"\nCANNOT START: lost the connection to {scenario.base_url} ({err}).", file=sys.stderr)
            return 2
        report_mismatch(scenario, f"no response from the API ({type(err).__name__}: {err})")
        return 1
    finally:
        scenario.close()
    print("\nAll seven steps matched the story.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
