"""ERPNext 种数脚本（RevGuard 0.6.0 决赛）。

在 erpnext-backend 容器内运行，把 data/fixtures/*.json 的比赛数据导入真实
ERPNext 站点，并创建只读 API 用户。设计约束：

- 种数身份是 Administrator（bench 直连数据库），与运行身份 revguard_api 分离；
- revguard_api 只被授予六个 DocType 的读权限，不能写任何单据；
- 幂等：按业务键查重，重复运行跳过已存在记录，不重复种数；
- ERPNext 只承载业务事实（伙伴/等级/合同/订单/发票/回款/退款），政策与
  佣金台账仍在 RevGuard 控制面，本脚本不写任何佣金调整。

用法：
    docker cp scripts/seed_erpnext.py <backend>:/home/frappe/frappe-bench/apps/erpnext/erpnext/revguard_seed.py
    docker cp data/fixtures <backend>:/tmp/fixtures
    docker exec -e REVGUARD_SEED_FIXTURES=/tmp/fixtures \
                 -e REVGUARD_SEED_OUTPUT=/tmp/revguard_api_keys.json <backend> \
        bash -lc "cd /home/frappe/frappe-bench && bench --site <site> execute erpnext.revguard_seed.main"

产物 /tmp/revguard_api_keys.json：{"api_key": ..., "api_secret": ...}，
仅限服务器上权限受限目录使用，禁止进入仓库、日志和证据包。
"""
from __future__ import annotations

import json
import os

import frappe

FIXTURES = os.getenv("REVGUARD_SEED_FIXTURES", "/tmp/fixtures")  # nosec B108 - 容器内临时种数目录，可被环境变量覆盖
OUTPUT = os.getenv("REVGUARD_SEED_OUTPUT", "/tmp/revguard_api_keys.json")  # nosec B108 - 同上，密钥产物 chmod 600
COMPANY = "RevGuard Demo Ltd"
API_USER = "revguard-api@revguard.local"
ROLE = "RevGuard API"

CUSTOM_FIELDS: list[dict] = [
    ("Sales Order", "custom_revguard_order_id", "RevGuard Order ID", "Data"),
    ("Sales Order", "custom_revguard_partner_id", "RevGuard Partner ID", "Data"),
    ("Sales Order", "custom_revguard_product_id", "RevGuard Product ID", "Data"),
    ("Sales Order", "custom_revguard_order_status", "RevGuard Order Status", "Data"),
    ("Sales Order", "custom_revguard_completed_date", "RevGuard Completed Date", "Date"),
    ("Sales Order", "custom_revguard_sales_owner", "RevGuard Sales Owner", "Data"),
    ("Sales Partner", "custom_revguard_partner_id", "RevGuard Partner ID", "Data"),
    ("Sales Partner", "custom_revguard_region", "RevGuard Region", "Data"),
    ("Contract", "custom_revguard_contract_id", "RevGuard Contract ID", "Data"),
    ("Contract", "custom_revguard_partner_id", "RevGuard Partner ID", "Data"),
    ("Contract", "custom_revguard_policy_id", "RevGuard Policy ID", "Data"),
    ("Contract", "custom_revguard_terms_json", "RevGuard Terms JSON", "Long Text"),
    ("Sales Invoice", "custom_revguard_order_id", "RevGuard Order ID", "Data"),
    ("Sales Invoice", "custom_revguard_invoice_id", "RevGuard Invoice ID", "Data"),
    ("Sales Invoice", "custom_revguard_refund_id", "RevGuard Refund ID", "Data"),
    ("Sales Invoice", "custom_revguard_refund_reason", "RevGuard Refund Reason", "Data"),
    ("Payment Entry", "custom_revguard_order_id", "RevGuard Order ID", "Data"),
    ("Payment Entry", "custom_revguard_payment_id", "RevGuard Payment ID", "Data"),
    ("Payment Entry", "custom_revguard_payment_status", "RevGuard Payment Status", "Data"),
]


def _load(name: str) -> list[dict]:
    with open(os.path.join(FIXTURES, name + ".json"), encoding="utf-8") as fh:
        return json.load(fh)


def _ensure_warehouse_types() -> None:
    # 域 fixtures 未安装时向导会因缺少 Transit 仓库类型而失败，先补齐
    for name in ("Transit",):
        if not frappe.db.exists("Warehouse Type", name):
            frappe.get_doc({
                "doctype": "Warehouse Type", "name": name,
            }).insert(ignore_permissions=True)


def _ensure_fiscal_year() -> None:
    existing = frappe.db.get_value(
        "Fiscal Year", {"year_start_date": "<= 2026-01-01",
                        "year_end_date": ">= 2026-12-31"}, "name")
    if not existing and not frappe.db.exists("Fiscal Year", "2026"):
        frappe.get_doc({
            "doctype": "Fiscal Year", "year": "2026",
            "year_start_date": "2026-01-01", "year_end_date": "2026-12-31",
        }).insert(ignore_permissions=True)


def _ensure_company() -> str:
    existing = frappe.db.get_value("Company", {}, "name")
    if existing:
        _ensure_fiscal_year()
        return existing
    # setup wizard 失败时会在内部回滚事务并连带吞掉预建数据，这里全部手工建：
    # Warehouse Type 先独立提交，再建公司（自动导入科目表/成本中心/默认仓库）
    _ensure_warehouse_types()
    frappe.db.commit()
    frappe.get_doc({
        "doctype": "Company", "company_name": COMPANY, "abbr": "RD",
        "country": "Kenya", "default_currency": "KES",
        "chart_of_accounts": "Standard",
    }).insert(ignore_permissions=True)
    frappe.db.commit()
    _ensure_fiscal_year()
    frappe.db.commit()
    name = frappe.db.get_value("Company", {}, "name")
    if not name:
        raise RuntimeError("公司初始化失败")
    return name


def _ensure_role() -> None:
    # 裸站点可能连标准角色都没有，一并补齐
    for role_name in ("System User", ROLE):
        if not frappe.db.exists("Role", role_name):
            frappe.get_doc({
                "doctype": "Role", "role_name": role_name,
                "desk_access": role_name == "System User",
            }).insert(ignore_permissions=True)
    frappe.db.commit()


def _ensure_doctype_permissions() -> None:
    from frappe.permissions import add_permission

    for doctype in (
        "Sales Order", "Sales Partner", "Contract",
        "Sales Invoice", "Payment Entry", "Partner Tier History",
    ):
        if not frappe.db.exists("DocPerm", {"parent": doctype, "role": ROLE}):
            add_permission(doctype, ROLE, 0, ptype="read")
    frappe.db.commit()


def _ensure_custom_doctype() -> None:
    if frappe.db.exists("DocType", "Partner Tier History"):
        return
    module = "Core"
    if not frappe.db.exists("Module Def", "Custom"):
        frappe.get_doc({"doctype": "Module Def", "module_name": "Custom",
                        "app_name": "frappe"}).insert(ignore_permissions=True)
        module = "Custom"
    else:
        module = "Custom"
    frappe.get_doc({
        "doctype": "DocType",
        "name": "Partner Tier History",
        "module": module,
        "custom": 1,
        "autoname": "format:{partner_id}-{tier}-{#####}",
        "fields": [
            {"fieldname": "partner_id", "fieldtype": "Data", "label": "Partner ID",
             "reqd": 1, "in_list_view": 1},
            {"fieldname": "tier", "fieldtype": "Data", "label": "Tier",
             "reqd": 1, "in_list_view": 1},
            {"fieldname": "effective_from", "fieldtype": "Date", "label": "Effective From",
             "reqd": 1, "in_list_view": 1},
        ],
        "permissions": [
            {"role": "System Manager", "read": 1, "write": 1, "create": 1, "delete": 1},
            {"role": ROLE, "read": 1},
        ],
    }).insert(ignore_permissions=True)
    frappe.db.commit()


def _ensure_custom_fields() -> None:
    for doctype, fieldname, label, fieldtype in CUSTOM_FIELDS:
        if frappe.db.exists("Custom Field", {"dt": doctype, "fieldname": fieldname}):
            continue
        frappe.get_doc({
            "doctype": "Custom Field", "dt": doctype, "fieldname": fieldname,
            "label": label, "fieldtype": fieldtype,
        }).insert(ignore_permissions=True)
    frappe.db.commit()


def _ensure_selling_masters() -> str:
    # 裸站点没有 setup wizard 的默认主数据，逐一补齐
    group = frappe.db.get_value("Item Group", {"is_group": 0}, "name")
    if not group:
        if not frappe.db.exists("Item Group", "All Item Groups"):
            frappe.get_doc({
                "doctype": "Item Group", "item_group_name": "All Item Groups",
                "is_group": 1,
            }).insert(ignore_permissions=True)
        group = "All Item Groups"
    if not frappe.db.exists("UOM", "Nos"):
        frappe.get_doc({"doctype": "UOM", "uom_name": "Nos"}).insert(
            ignore_permissions=True)
    if not frappe.db.exists("Customer Group", "All Customer Groups"):
        frappe.get_doc({
            "doctype": "Customer Group",
            "customer_group_name": "All Customer Groups", "is_group": 1,
        }).insert(ignore_permissions=True)
    if not frappe.db.exists("Customer Group", "RevGuard Partners"):
        frappe.get_doc({
            "doctype": "Customer Group", "customer_group_name": "RevGuard Partners",
            "parent_customer_group": "All Customer Groups", "is_group": 0,
        }).insert(ignore_permissions=True)
    if not frappe.db.exists("Territory", "All Territories"):
        frappe.get_doc({
            "doctype": "Territory", "territory_name": "All Territories",
            "is_group": 1,
        }).insert(ignore_permissions=True)
    if not frappe.db.exists("Territory", "Kenya"):
        frappe.get_doc({
            "doctype": "Territory", "territory_name": "Kenya",
            "parent_territory": "All Territories", "is_group": 0,
        }).insert(ignore_permissions=True)
    if not frappe.db.exists("Price List", "RevGuard KES"):
        frappe.get_doc({
            "doctype": "Price List", "price_list_name": "RevGuard KES",
            "currency": "KES", "selling": 1, "buying": 0, "enabled": 1,
        }).insert(ignore_permissions=True)
    frappe.db.commit()
    return group


def _cash_account(company: str) -> str:
    for pattern in ({"account_type": "Cash"}, {"account_name": ("like", "%Cash%")},
                    {"account_name": ("like", "%Bank%")}):
        name = frappe.db.get_value(
            "Account", {"company": company, "is_group": 0, **pattern}, "name")
        if name:
            return name
    raise RuntimeError("未找到现金/银行账户，请检查科目表")


def _receivable_account(company: str) -> str:
    name = frappe.db.get_value("Company", company, "default_receivable_account")
    if name:
        return name
    name = frappe.db.get_value(
        "Account", {"company": company, "is_group": 0, "account_type": "Receivable"},
        "name")
    if not name:
        raise RuntimeError("未找到应收账款账户，请检查科目表")
    return name


def _customer_name(partner: dict) -> str:
    return partner["name"]


def _try_submit(doc) -> bool:
    try:
        doc.submit()
        return True
    except Exception as exc:  # noqa: BLE001 - 单据提交失败不阻断种数，保留 Draft 供读取
        print(f"  [warn] {doc.doctype} {doc.name} 提交失败，保留 Draft: {exc}")
        return False


def main() -> None:
    frappe.flags.ignore_permissions = True
    company = _ensure_company()
    print(f"company: {company}")

    _ensure_role()
    _ensure_custom_doctype()
    _ensure_custom_fields()
    _ensure_doctype_permissions()

    partners = _load("partners")
    orders = {o["order_id"]: o for o in _load("orders")}
    invoices = _load("invoices")
    payments = _load("payments")
    refunds = _load("refunds")
    contracts = _load("contracts")

    # 商品（非库存项，避免提交时触发库存台账）
    item_group = _ensure_selling_masters()
    products = sorted({o.get("product_id") for o in orders.values() if o.get("product_id")})
    for code in products:
        if not frappe.db.exists("Item", code):
            frappe.get_doc({
                "doctype": "Item", "item_code": code, "item_name": code,
                "item_group": item_group, "stock_uom": "Nos", "is_stock_item": 0,
                "is_sales_item": 1,
            }).insert(ignore_permissions=True)

    # 渠道伙伴：Customer + Sales Partner + 等级历史 + 合同
    customer_of: dict[str, str] = {}
    for partner in partners:
        cust = frappe.db.get_value(
            "Customer", {"customer_name": _customer_name(partner)}, "name")
        if not cust:
            cust = frappe.get_doc({
                "doctype": "Customer", "customer_name": _customer_name(partner),
                "customer_type": "Company",
                "customer_group": "RevGuard Partners",
                "territory": "Kenya",
                "custom_revguard_partner_id": partner["partner_id"],
                "custom_revguard_region": partner["region"],
            }).insert(ignore_permissions=True).name
        customer_of[partner["partner_id"]] = cust

        if not frappe.db.exists("Sales Partner", {"partner_name": _customer_name(partner)}):
            frappe.get_doc({
                "doctype": "Sales Partner", "partner_name": _customer_name(partner),
                "territory": "Kenya",
                "commission_rate": 0,
                "custom_revguard_partner_id": partner["partner_id"],
                "custom_revguard_region": partner["region"],
            }).insert(ignore_permissions=True)

        for tier in partner.get("tier_history", []):
            key = {
                "partner_id": partner["partner_id"],
                "tier": tier["tier"],
                "effective_from": tier["effective_from"],
            }
            if not frappe.db.exists("Partner Tier History", key):
                frappe.get_doc({
                    "doctype": "Partner Tier History", **key,
                }).insert(ignore_permissions=True)

    for contract in contracts:
        if frappe.db.exists("Contract",
                            {"custom_revguard_contract_id": contract["contract_id"]}):
            continue
        frappe.get_doc({
            "doctype": "Contract",
            "party_type": "Customer",
            "party_name": customer_of[contract["partner_id"]],
            "start_date": contract["signed_date"],
            "contract_terms": json.dumps(
                contract.get("terms", {}), ensure_ascii=False),
            "custom_revguard_contract_id": contract["contract_id"],
            "custom_revguard_partner_id": contract["partner_id"],
            "custom_revguard_policy_id": contract["policy_id"],
            "custom_revguard_terms_json": json.dumps(
                contract.get("terms", {}), ensure_ascii=False),
        }).insert(ignore_permissions=True)
    frappe.db.commit()

    # 订单：ERP 观察佣金统一按 10%（SILVER 口径），RevGuard 复算发现应为 18%（GOLD）
    for order in orders.values():
        if frappe.db.exists("Sales Order",
                            {"custom_revguard_order_id": order["order_id"]}):
            continue
        observed_rate = 10
        doc = frappe.get_doc({
            "doctype": "Sales Order",
            "customer": customer_of[order["partner_id"]],
            "company": company,
            "transaction_date": order["order_date"],
            "delivery_date": order.get("completed_date") or order["order_date"],
            "currency": order["currency"],
            "selling_price_list": "RevGuard KES",
            "price_list_currency": "KES",
            "plc_conversion_rate": 1,
            "items": [{
                "item_code": order["product_id"], "qty": 1,
                "rate": order["order_amount"],
            }],
            "sales_partner": _customer_name(next(
                p for p in partners if p["partner_id"] == order["partner_id"])),
            "commission_rate": observed_rate,
            "total_commission": order["order_amount"] * observed_rate / 100,
            "custom_revguard_order_id": order["order_id"],
            "custom_revguard_partner_id": order["partner_id"],
            "custom_revguard_product_id": order["product_id"],
            "custom_revguard_order_status": order["order_status"],
            "custom_revguard_completed_date": order.get("completed_date"),
            "custom_revguard_sales_owner": order.get("sales_owner"),
        })
        doc.insert(ignore_permissions=True)
        if order["order_status"] == "COMPLETED":
            _try_submit(doc)

    # 发票（正向）
    for inv in invoices:
        if frappe.db.exists("Sales Invoice",
                            {"custom_revguard_invoice_id": inv["invoice_id"]}):
            continue
        order = orders[inv["order_id"]]
        doc = frappe.get_doc({
            "doctype": "Sales Invoice",
            "customer": customer_of[order["partner_id"]],
            "company": company,
            "posting_date": inv["invoice_date"],
            "currency": inv["currency"],
            "selling_price_list": "RevGuard KES",
            "price_list_currency": "KES",
            "plc_conversion_rate": 1,
            "is_return": 0,
            "items": [{
                "item_code": order["product_id"], "qty": 1,
                "rate": inv["invoice_amount"],
            }],
            "custom_revguard_order_id": inv["order_id"],
            "custom_revguard_invoice_id": inv["invoice_id"],
        })
        doc.insert(ignore_permissions=True)
        _try_submit(doc)

    # 退款（负向发票 / Credit Note）
    for refund in refunds:
        if frappe.db.exists("Sales Invoice",
                            {"custom_revguard_refund_id": refund["refund_id"]}):
            continue
        order = orders[refund["order_id"]]
        doc = frappe.get_doc({
            "doctype": "Sales Invoice",
            "customer": customer_of[order["partner_id"]],
            "company": company,
            "posting_date": refund["refund_date"],
            "currency": refund["currency"],
            "is_return": 1,
            "update_stock": 0,
            "items": [{
                "item_code": order["product_id"], "qty": -1,
                "rate": refund["refund_amount"],
            }],
            "custom_revguard_order_id": refund["order_id"],
            "custom_revguard_refund_id": refund["refund_id"],
            "custom_revguard_refund_reason": refund["reason"],
        })
        doc.insert(ignore_permissions=True)
        _try_submit(doc)

    # 回款
    account = _cash_account(company)
    receivable = _receivable_account(company)
    for pay in payments:
        if frappe.db.exists("Payment Entry",
                            {"custom_revguard_payment_id": pay["payment_id"]}):
            continue
        order = orders[pay["order_id"]]
        doc = frappe.get_doc({
            "doctype": "Payment Entry",
            "company": company,
            "payment_type": "Receive",
            "party_type": "Customer",
            "party": customer_of[order["partner_id"]],
            "posting_date": pay["payment_date"],
            "paid_from": receivable,
            "paid_amount": pay["payment_amount"],
            "received_amount": pay["payment_amount"],
            "paid_to": account,
            "custom_revguard_order_id": pay["order_id"],
            "custom_revguard_payment_id": pay["payment_id"],
            "custom_revguard_payment_status": pay["payment_status"],
        })
        doc.insert(ignore_permissions=True)
        _try_submit(doc)
    frappe.db.commit()

    # 只读 API 用户 + Token
    if not frappe.db.exists("User", API_USER):
        frappe.get_doc({
            "doctype": "User", "email": API_USER, "first_name": "RevGuard API",
            "enabled": 1, "user_type": "System User",
            "roles": [{"role": "System User"}, {"role": ROLE}],
        }).insert(ignore_permissions=True)
        # 有 desk_access=0 角色时 frappe 会把 user_type 回写为 Website User，
        # 直接在 db 层强制 System User + 系统角色，绕过 validate 推导
        if not frappe.db.exists("Has Role", {"parent": API_USER, "parenttype": "User",
                                             "parentfield": "roles",
                                             "role": "System User"}):
            frappe.get_doc({
                "doctype": "Has Role", "parent": API_USER, "parenttype": "User",
                "parentfield": "roles", "role": "System User",
            }).db_insert()
        frappe.db.set_value("User", API_USER, "user_type", "System User")
    from frappe.core.doctype.user.user import generate_keys
    from frappe.utils.password import get_decrypted_password

    generate_keys(API_USER)
    api_key = frappe.db.get_value("User", API_USER, "api_key")
    # generate_keys 的返回值可能与落库值不同步，文件必须写 DB 里的权威值
    stored = frappe.db.get_value("User", API_USER, "api_secret") or ""
    decrypted = get_decrypted_password(
        "User", API_USER, fieldname="api_secret", raise_exception=False) or ""
    api_secret = decrypted or stored
    frappe.db.commit()
    with open(OUTPUT, "w", encoding="utf-8") as fh:
        json.dump({"api_key": api_key, "api_secret": api_secret}, fh)
    os.chmod(OUTPUT, 0o600)

    print("seeded:")
    for doctype in ("Customer", "Sales Partner", "Partner Tier History", "Contract",
                    "Sales Order", "Sales Invoice", "Payment Entry"):
        print(f"  {doctype}: {frappe.db.count(doctype)}")
if os.getenv("REVGUARD_SEED_AUTORUN", "1") != "0":
    main()
