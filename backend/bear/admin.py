"""Staff pages under /admin for creating and editing repair orders.

This is the MVP of the shop's admin side. It lives in its own router so a dashboard
(metrics, customers, an estimator) can grow here later without touching the voice tools.

Every page sits behind the same HTTP Basic login as the live board. Browsers send Basic
credentials on cross-site form posts too, so every POST also needs a CSRF token tied to
the shop and signed with a key derived from ADMIN_PASSWORD.
"""

import hashlib
import hmac
import html
import time
from collections.abc import Callable
from datetime import date, datetime
from datetime import time as clock_time
from typing import Annotated
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, BeforeValidator, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from bear.eta import spoken_eta
from bear.shops import ShopConfig
from bear.store import Store
from bear.validation import Name, OptionalText, Phone, clean_text

CSRF_MAX_AGE_SECONDS = 12 * 60 * 60
MAX_FORM_BYTES = 16 * 1024
FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"
CLOSED_LIST_LIMIT = 20

FIELD_LABELS = {
    "ro_number": "Repair order number",
    "customer_name": "Customer name",
    "phone": "Phone",
    "vehicle": "Vehicle",
    "plate": "Plate",
    "status": "Status",
    "eta_date": "Done by (date)",
    "eta_time": "Done by (time)",
    "eta_note": "ETA note",
    "note": "Work notes",
    "balance_estimate": "Estimated balance",
}
FIELD_HINTS = {
    "ro_number": "Use 1-20 letters, digits or dashes.",
    "phone": "Enter a 10-digit US number, or + and the country code.",
    "eta_date": "Pick a date.",
    "eta_time": "Use a time like 15:00.",
}


class CsrfSigner:
    """Stateless CSRF tokens: '<issued unix seconds>.<hmac(shop_id, issued)>'."""

    def __init__(self, admin_password: str):
        self.key = hashlib.sha256(b"bear-admin-csrf:" + admin_password.encode("utf-8")).digest()

    def _signature(self, shop_id: str, issued: int) -> str:
        return hmac.new(self.key, f"{shop_id}:{issued}".encode(), hashlib.sha256).hexdigest()

    def issue(self, shop_id: str, now: float | None = None) -> str:
        issued = int(time.time() if now is None else now)
        return f"{issued}.{self._signature(shop_id, issued)}"

    def verify(self, token: str, shop_id: str, now: float | None = None) -> bool:
        issued_text, _, signature = (token or "").partition(".")
        if not issued_text.isdigit():
            return False
        issued = int(issued_text)
        age = int(time.time() if now is None else now) - issued
        if not 0 <= age <= CSRF_MAX_AGE_SECONDS:
            return False
        return hmac.compare_digest(signature, self._signature(shop_id, issued))


ShortText = Annotated[str, Field(max_length=20), BeforeValidator(clean_text)]
PlateText = Annotated[str, Field(max_length=16), BeforeValidator(clean_text)]
EtaNote = Annotated[str, Field(max_length=200), BeforeValidator(clean_text)]


def blank_to_none(value):
    return None if isinstance(value, str) and not value.strip() else value


class RepairOrderForm(BaseModel):
    ro_number: Annotated[str, BeforeValidator(clean_text)] = Field(pattern=r"^[A-Za-z0-9-]{1,20}$")
    customer_name: Name
    phone: Phone
    vehicle: Name
    plate: PlateText = ""
    status: str
    eta_date: Annotated[date | None, BeforeValidator(blank_to_none)] = None
    eta_time: Annotated[clock_time | None, BeforeValidator(blank_to_none)] = None
    eta_note: EtaNote = ""
    note: OptionalText = ""
    balance_estimate: ShortText = ""


def form_errors(error: ValidationError) -> dict[str, str]:
    messages = {}
    for item in error.errors():
        field = str(item["loc"][0]) if item["loc"] else "form"
        label = FIELD_LABELS.get(field, field)
        messages.setdefault(field, f"{label}: {FIELD_HINTS.get(field, 'please check this field.')}")
    return messages


def escape(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


PAGE_STYLE = """body{font-family:system-ui;margin:24px;max-width:1100px}table{border-collapse:collapse;margin-bottom:24px}
td,th{border:1px solid #ccc;padding:6px 10px;text-align:left}th{background:#222;color:#fff}
form.ro label{display:block;margin:10px 0 2px;font-weight:600}form.ro input,form.ro select,form.ro textarea{width:28rem;max-width:100%;padding:6px}
.errors{background:#f6c1c1;padding:8px 12px}.saved{background:#c9f2c9;padding:8px 12px}.past-due{background:#ffe08a;font-weight:700}
button{margin-top:16px;padding:8px 18px}"""


def page(title: str, body: str) -> HTMLResponse:
    document = (
        f"<!doctype html><html><head><meta charset='utf-8'><title>{escape(title)}</title>"
        f"<style>{PAGE_STYLE}</style></head><body>{body}</body></html>"
    )
    return HTMLResponse(document, headers={"Cache-Control": "no-store"})


def build_admin_router(
    *,
    store: Store,
    shops: dict[str, ShopConfig],
    default_shop_id: str,
    admin_password: str,
    check_admin: Callable,
    status_labels: dict[str, str],
    closed_status: str,
) -> APIRouter:
    router = APIRouter(prefix="/admin/ros", dependencies=[Depends(check_admin)])
    signer = CsrfSigner(admin_password)

    def resolve(shop_id: str | None) -> ShopConfig:
        shop = shops.get(default_shop_id if shop_id is None else shop_id)
        if shop is None:
            raise HTTPException(status_code=404, detail="unknown shop")
        return shop

    def list_url(shop: ShopConfig, **extra) -> str:
        return "/admin/ros?" + urlencode({"shop": shop.shop_id, **extra})

    def eta_cell(order: dict, shop: ShopConfig, now: datetime) -> str:
        if order["eta_at"] is None:
            return escape(order["eta"])
        spoken = spoken_eta(order["eta_at"], now, shop.tz)
        past_due = order["eta_at"] < now and order["status"] not in {closed_status, "ready_for_pickup"}
        css = ' class="past-due"' if past_due else ""
        note = f" ({escape(order['eta'])})" if order["eta"] else ""
        return f"<span{css}>{escape(spoken)}{note}</span>"

    def orders_table(orders: list[dict], shop: ShopConfig, now: datetime) -> str:
        if not orders:
            return "<p>None.</p>"
        header = "".join(
            f"<th>{label}</th>" for label in ("RO", "Customer", "Vehicle", "Status", "Done by", "Updated", "")
        )
        rows = []
        for order in orders:
            edit = "/admin/ros/edit?" + urlencode({"shop": shop.shop_id, "ro": order["ro_number"]})
            updated = order["updated_at"].astimezone(shop.tz).strftime("%Y-%m-%d %H:%M %Z")
            cells = (
                escape(order["ro_number"]),
                escape(order["customer_name"]),
                escape(order["vehicle"]),
                escape(status_labels.get(order["status"], order["status"])),
                eta_cell(order, shop, now),
                escape(updated),
                f'<a href="{escape(edit)}">Edit</a>',
            )
            rows.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
        return f"<table><tr>{header}</tr>{''.join(rows)}</table>"

    def form_page(shop: ShopConfig, values: dict, mode: str, errors: dict[str, str] | None = None) -> str:
        def field(name, input_html):
            return f'<label for="{name}">{escape(FIELD_LABELS[name])}</label>{input_html}'

        def text_input(name, maxlength, *, required=False, readonly=False, input_type="text"):
            flags = (" required" if required else "") + (" readonly" if readonly else "")
            return field(
                name,
                f'<input id="{name}" name="{name}" type="{input_type}" maxlength="{maxlength}" '
                f'value="{escape(values.get(name, ""))}"{flags}>',
            )

        options = "".join(
            f'<option value="{escape(key)}"{" selected" if values.get("status") == key else ""}>{escape(label)}</option>'
            for key, label in status_labels.items()
        )
        error_html = ""
        if errors:
            error_html = (
                '<div class="errors"><ul>' + "".join(f"<li>{escape(m)}</li>" for m in errors.values()) + "</ul></div>"
            )
        title = "New repair order" if mode == "create" else f"Edit repair order {values.get('ro_number', '')}"
        return (
            f"<h1>{escape(shop.name)}: {escape(title)}</h1>{error_html}"
            f'<form class="ro" method="post" action="{escape(list_url(shop))}">'
            f'<input type="hidden" name="csrf_token" value="{escape(signer.issue(shop.shop_id))}">'
            f'<input type="hidden" name="mode" value="{escape(mode)}">'
            + text_input("ro_number", 20, required=True, readonly=mode == "edit")
            + text_input("customer_name", 80, required=True)
            + text_input("phone", 32, required=True, input_type="tel")
            + text_input("vehicle", 80, required=True)
            + text_input("plate", 16)
            + field("status", f'<select id="status" name="status">{options}</select>')
            + text_input("eta_date", 10, input_type="date")
            + text_input("eta_time", 5, input_type="time")
            + text_input("eta_note", 200)
            + field(
                "note",
                f'<textarea id="note" name="note" maxlength="500" rows="3">{escape(values.get("note", ""))}</textarea>',
            )
            + text_input("balance_estimate", 20)
            + f'<br><button type="submit">Save</button> <a href="{escape(list_url(shop))}">Cancel</a></form>'
            f"<p>Bear reads the status, the done-by time and the ETA note to callers who verify with their last "
            f"name plus the phone number or repair order number. Mark the car picked up to close the order.</p>"
        )

    @router.get("", response_class=HTMLResponse)
    def list_repair_orders(shop: str | None = None, saved: str | None = None):
        shop_config = resolve(shop)
        orders = store.list_repair_orders(shop_config.shop_id)
        now = datetime.now(shop_config.tz)
        open_orders = [order for order in orders if order["status"] != closed_status]
        closed_orders = [order for order in orders if order["status"] == closed_status][:CLOSED_LIST_LIMIT]
        notice = f'<p class="saved">Saved repair order {escape(saved)}.</p>' if saved else ""
        board = "/admin?" + urlencode({"shop": shop_config.shop_id})
        new = "/admin/ros/new?" + urlencode({"shop": shop_config.shop_id})
        body = (
            f"<h1>{escape(shop_config.name)}: repair orders</h1>{notice}"
            f'<p><a href="{escape(new)}">+ New repair order</a> &middot; <a href="{escape(board)}">Live board</a></p>'
            f"<h2>Open</h2>{orders_table(open_orders, shop_config, now)}"
            f"<h2>Picked up (latest {CLOSED_LIST_LIMIT})</h2>{orders_table(closed_orders, shop_config, now)}"
        )
        return page(f"{shop_config.name} repair orders", body)

    @router.get("/new", response_class=HTMLResponse)
    def new_repair_order(shop: str | None = None):
        shop_config = resolve(shop)
        defaults = {"status": next(iter(status_labels)), "eta_time": "17:00"}
        return page("New repair order", form_page(shop_config, defaults, "create"))

    @router.get("/edit", response_class=HTMLResponse)
    def edit_repair_order(ro: str, shop: str | None = None):
        shop_config = resolve(shop)
        order = store.get_repair_order(shop_config.shop_id, ro)
        if order is None:
            raise HTTPException(status_code=404, detail="unknown repair order")
        values = {**order, "eta_note": order["eta"]}
        if order["eta_at"] is not None:
            local = order["eta_at"].astimezone(shop_config.tz)
            values.update(eta_date=local.date().isoformat(), eta_time=local.strftime("%H:%M"))
        return page(f"Edit repair order {ro}", form_page(shop_config, values, "edit"))

    @router.post("", response_class=HTMLResponse)
    async def save_repair_order(request: Request, shop: str | None = None):
        shop_config = resolve(shop)
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.headers.get("host"):
            raise HTTPException(status_code=403, detail="cross-site form post")
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != FORM_CONTENT_TYPE:
            raise HTTPException(status_code=415, detail="expected a form post")
        body = await request.body()
        if len(body) > MAX_FORM_BYTES:
            raise HTTPException(status_code=413, detail="form too large")
        try:
            raw = {key: values[0] for key, values in parse_qs(body.decode("utf-8"), keep_blank_values=True).items()}
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="invalid form encoding") from None
        if not signer.verify(raw.get("csrf_token", ""), shop_config.shop_id):
            raise HTTPException(status_code=403, detail="expired or invalid form; reload the page and try again")
        mode = raw.get("mode")
        if mode not in {"create", "edit"}:
            raise HTTPException(status_code=400, detail="invalid form")

        def show_errors(errors: dict[str, str]):
            response = page("Fix the repair order", form_page(shop_config, raw, mode, errors))
            response.status_code = 400
            return response

        try:
            form = RepairOrderForm.model_validate(raw)
        except ValidationError as error:
            return show_errors(form_errors(error))
        errors = {}
        if form.status not in status_labels:
            errors["status"] = "Status: pick one from the list."
        if (form.eta_date is None) != (form.eta_time is None):
            errors["eta_date"] = "Done by: fill in both the date and the time, or leave both blank."
        existing = await run_in_threadpool(store.get_repair_order, shop_config.shop_id, form.ro_number)
        if mode == "create" and existing is not None:
            errors["ro_number"] = f"Repair order {form.ro_number} already exists. Edit it from the list instead."
        if mode == "edit" and existing is None:
            errors["ro_number"] = f"Repair order {form.ro_number} doesn't exist anymore."
        if errors:
            return show_errors(errors)

        eta_at = None
        if form.eta_date is not None and form.eta_time is not None:
            eta_at = datetime.combine(form.eta_date, form.eta_time, tzinfo=shop_config.tz)
        await run_in_threadpool(
            lambda: store.upsert_repair_order(
                shop_config.shop_id,
                audit_event="repair_order.created" if mode == "create" else "repair_order.updated",
                ro_number=form.ro_number,
                customer_name=form.customer_name,
                phone=form.phone,
                plate=form.plate,
                vehicle=form.vehicle,
                status=form.status,
                note=form.note,
                eta=form.eta_note,
                eta_at=eta_at,
                balance_estimate=form.balance_estimate,
            )
        )
        return RedirectResponse(list_url(shop_config, saved=form.ro_number), status_code=303)

    return router
