
from mcp.server.mcpserver import MCPServer
from ax_db import (DEFAULT_COMPANY, SALES_STATUS, SALES_TYPE, PURCH_STATUS,
                   NeedClarification, ax_tool, ok, ask, query, connect,
                   company_of, limit_of, date_range, check_select_only)

mcp = MCPServer(
    name="dynamics-erp",
    instructions=(
        "Tools for the user's live Microsoft Dynamics AX 2012 database (read-only). "
        f"Default company (DATAAREAID) is {DEFAULT_COMPANY}; list_companies shows all. "
        "Prefer the ready-made report tools. For anything else, use find_tables -> "
        "describe_table -> run_sql_query, and always filter by DATAAREAID. "
        "If a tool returns 'need_clarification', ask the user the 'ask_user' question first. "
        "If it returns 'not_connected', tell the user AX is unreachable; never invent data. "
        "Amounts are in company currency (MST). Negative sales amounts are returns/credits."
    ),
)


NAME_JOIN = "JOIN DIRPARTYTABLE p ON p.RECID = {t}.PARTY"


def find_status(text, enum):
    """Map a status name to its AX enum value."""
    if not text:
        return None
    for num, name in enum.items():
        if text.strip().lower() == name.lower():
            return num
    raise NeedClarification(f"Unknown status '{text}'. Which one?", list(enum.values())[1:])




@mcp.tool()
@ax_tool
def connection_status() -> dict:
    """Check whether the server is connected to the live AX 2012 database."""
    connect().close()
    return {"status": "ok", "connected_to_real_ax": True, "default_company": DEFAULT_COMPANY}


@mcp.tool()
@ax_tool
def list_companies() -> dict:
    """List the companies / legal entities (DATAAREAID) in the user's connected AX 2012.
    Use whenever the user asks which companies or entities exist in 'AX', 'my AX' or 'the ERP'."""
    rows = query("SELECT ID, NAME, ISVIRTUAL FROM DATAAREA ORDER BY ID")
    for r in rows:
        r["ISVIRTUAL"] = bool(r["ISVIRTUAL"])
    return ok(rows, default_company=DEFAULT_COMPANY)




@mcp.tool()
@ax_tool
def search_customers(text: str, company: str = "", limit: int = 10) -> dict:
    """Find customers by part of their name or account number.
    Use this first when the user gives a customer name instead of an account.
    company = DATAAREAID (empty = default). company='ALL' searches every company."""
    text = text.strip()
    if len(text) < 2:
        return ask("Which customer? Give at least 2 letters of the name or the account number.")
    company = company_of(company)
    sql = f"""SELECT TOP (?) c.ACCOUNTNUM, p.NAME, c.CUSTGROUP, c.CURRENCY,
                     c.CREDITMAX, c.DATAAREAID AS COMPANY
              FROM CUSTTABLE c {NAME_JOIN.format(t='c')}
              WHERE (p.NAME LIKE ? OR c.ACCOUNTNUM LIKE ?)"""
    params = [limit_of(limit, 50), f"%{text}%", f"%{text}%"]
    if company != "ALL":
        sql += " AND c.DATAAREAID = ?"
        params.append(company)
    rows = query(sql + " ORDER BY p.NAME", params)

    where = "any company" if company == "ALL" else company
    if not rows:
        return ask(f"No customer found for '{text}' in {where}. Check spelling or search all companies?")
    if len(rows) > 1:
        return ask(f"{len(rows)} customers match '{text}' in {where}. Which one?",
                   [f"{r['ACCOUNTNUM']} - {r['NAME']} ({r['COMPANY']})" for r in rows])
    return ok(rows[0])


@mcp.tool()
@ax_tool
def customer_balance(customer_account: str = "", company: str = "", top: int = 20) -> dict:
    """Customer balance and AGING report (receivables) from open customer transactions.
    With customer_account: that customer's balance split by days overdue.
    Without it: the top customers by outstanding balance (top debtors).
    Buckets: not due, 1-30, 31-60, 61-90, over 90 days past due date."""
    company = company_of(company)
    days = "DATEDIFF(day, t.DUEDATE, GETDATE())"
    open_amt = "(t.AMOUNTMST - t.SETTLEAMOUNTMST)"
    sql = f"""
        SELECT TOP (?) t.ACCOUNTNUM, p.NAME,
          SUM({open_amt}) AS BALANCE,
          SUM(CASE WHEN t.DUEDATE < '1901-01-01' OR {days} <= 0 THEN {open_amt} ELSE 0 END) AS NOT_DUE,
          SUM(CASE WHEN t.DUEDATE >= '1901-01-01' AND {days} BETWEEN 1 AND 30 THEN {open_amt} ELSE 0 END) AS DAYS_1_30,
          SUM(CASE WHEN t.DUEDATE >= '1901-01-01' AND {days} BETWEEN 31 AND 60 THEN {open_amt} ELSE 0 END) AS DAYS_31_60,
          SUM(CASE WHEN t.DUEDATE >= '1901-01-01' AND {days} BETWEEN 61 AND 90 THEN {open_amt} ELSE 0 END) AS DAYS_61_90,
          SUM(CASE WHEN t.DUEDATE >= '1901-01-01' AND {days} > 90 THEN {open_amt} ELSE 0 END) AS OVER_90
        FROM CUSTTRANS t
        JOIN CUSTTABLE c ON c.ACCOUNTNUM = t.ACCOUNTNUM AND c.DATAAREAID = t.DATAAREAID
        {NAME_JOIN.format(t='c')}
        WHERE t.DATAAREAID = ? AND t.AMOUNTMST <> t.SETTLEAMOUNTMST"""
    params = [limit_of(top, 100), company]
    if customer_account:
        sql += " AND t.ACCOUNTNUM = ?"
        params.append(customer_account.strip())
    sql += " GROUP BY t.ACCOUNTNUM, p.NAME ORDER BY BALANCE DESC"
    rows = query(sql, params)
    if not rows:
        if customer_account:
            return ok([], message=f"{customer_account} has no open balance in {company}.")
        return ok([], message=f"No open customer balances in {company}.")
    return ok(rows, company=company, as_of="today",
              total_balance=round(sum(r["BALANCE"] for r in rows), 2),
              note="Aging is by open transaction. Negative amounts in a bucket are "
                   "payments or credit notes not yet settled against invoices; "
                   "settling them in AX would reduce the overdue buckets.")




@mcp.tool()
@ax_tool
def list_sales_orders(customer_account: str = "", status: str = "",
                      company: str = "", limit: int = 20) -> dict:
    """List the latest sales orders with their amount.
    Filter by customer_account (e.g. US-0001) and/or status: 'Open order',
    'Delivered', 'Invoiced', 'Canceled'. If you only know a customer name,
    call search_customers first."""
    company = company_of(company)
    sql = """SELECT TOP (?) s.SALESID, s.CUSTACCOUNT, s.SALESNAME, s.SALESSTATUS,
                    s.SALESTYPE, s.CREATEDDATETIME AS CREATED,
                    (SELECT SUM(l.LINEAMOUNT) FROM SALESLINE l
                     WHERE l.SALESID = s.SALESID AND l.DATAAREAID = s.DATAAREAID) AS AMOUNT
             FROM SALESTABLE s WHERE s.DATAAREAID = ?"""
    params = [limit_of(limit, 100), company]
    if customer_account:
        sql += " AND s.CUSTACCOUNT = ?"
        params.append(customer_account.strip())
    status_num = find_status(status, SALES_STATUS)
    if status_num is not None:
        sql += " AND s.SALESSTATUS = ?"
        params.append(status_num)
    rows = query(sql + " ORDER BY s.CREATEDDATETIME DESC", params)
    for r in rows:
        r["STATUS"] = SALES_STATUS.get(r.pop("SALESSTATUS"), "Unknown")
        r["TYPE"] = SALES_TYPE.get(r.pop("SALESTYPE"), "Unknown")
        r["AMOUNT"] = r["AMOUNT"] or 0.0
    if not rows:
        return ok([], message=f"No matching sales orders in {company}.")
    return ok(rows, company=company, count=len(rows),
              total_amount=round(sum(r["AMOUNT"] for r in rows), 2))


@mcp.tool()
@ax_tool
def sales_order_lines(sales_id: str, company: str = "") -> dict:
    """Show the lines (items, quantity, price, amount) of one sales order, e.g. SO-000123."""
    company = company_of(company)
    rows = query("""SELECT l.LINENUM, l.ITEMID, l.NAME, l.SALESQTY, l.SALESUNIT,
                           l.SALESPRICE, l.LINEAMOUNT, l.SALESSTATUS
                    FROM SALESLINE l
                    WHERE l.SALESID = ? AND l.DATAAREAID = ?
                    ORDER BY l.LINENUM""", [sales_id.strip(), company])
    if not rows:
        return ask(f"No sales order '{sales_id}' in {company}. Check the number or the company?")
    for r in rows:
        r["SALESSTATUS"] = SALES_STATUS.get(r["SALESSTATUS"], "Unknown")
    return ok(rows, sales_id=sales_id, company=company,
              total_amount=round(sum(r["LINEAMOUNT"] for r in rows), 2))


@mcp.tool()
@ax_tool
def sales_summary(group_by: str = "month", from_date: str = "", to_date: str = "",
                  company: str = "", top: int = 20) -> dict:
    """INVOICED SALES REPORT for a date range (from posted customer invoices).
    group_by: 'month' (sales trend), 'customer' (top customers) or 'item' (top items).
    Dates as YYYY-MM-DD; default is the last 30 days. Good for charts."""
    company = company_of(company)
    start, end = date_range(from_date, to_date)
    group_by = group_by.strip().lower()
    top = limit_of(top, 100)

    if group_by == "month":
        rows = query("""SELECT YEAR(j.INVOICEDATE) AS YEAR, MONTH(j.INVOICEDATE) AS MONTH,
                               COUNT(*) AS INVOICES, SUM(j.INVOICEAMOUNTMST) AS SALES
                        FROM CUSTINVOICEJOUR j
                        WHERE j.DATAAREAID = ? AND j.INVOICEDATE BETWEEN ? AND ?
                        GROUP BY YEAR(j.INVOICEDATE), MONTH(j.INVOICEDATE)
                        ORDER BY YEAR, MONTH""", [company, start, end])
    elif group_by == "customer":
        rows = query(f"""SELECT TOP (?) j.INVOICEACCOUNT AS ACCOUNTNUM, p.NAME,
                                COUNT(*) AS INVOICES, SUM(j.INVOICEAMOUNTMST) AS SALES
                         FROM CUSTINVOICEJOUR j
                         JOIN CUSTTABLE c ON c.ACCOUNTNUM = j.INVOICEACCOUNT AND c.DATAAREAID = j.DATAAREAID
                         {NAME_JOIN.format(t='c')}
                         WHERE j.DATAAREAID = ? AND j.INVOICEDATE BETWEEN ? AND ?
                         GROUP BY j.INVOICEACCOUNT, p.NAME
                         ORDER BY SALES DESC""", [top, company, start, end])
    elif group_by == "item":
        rows = query("""SELECT TOP (?) t.ITEMID, MAX(t.NAME) AS NAME,
                               SUM(t.QTY) AS QTY, SUM(t.LINEAMOUNTMST) AS SALES
                        FROM CUSTINVOICETRANS t
                        WHERE t.DATAAREAID = ? AND t.INVOICEDATE BETWEEN ? AND ?
                        GROUP BY t.ITEMID
                        ORDER BY SALES DESC""", [top, company, start, end])
    else:
        return ask("How should I group the sales report?", ["month", "customer", "item"])

    return ok(rows, company=company, period=f"{start} to {end}", group_by=group_by,
              total_sales=round(sum(r["SALES"] or 0 for r in rows), 2))




@mcp.tool()
@ax_tool
def search_vendors(text: str, company: str = "", limit: int = 10) -> dict:
    """Find vendors (suppliers) by part of their name or account number.
    company = DATAAREAID (empty = default). company='ALL' searches every company."""
    text = text.strip()
    if len(text) < 2:
        return ask("Which vendor? Give at least 2 letters of the name or the account number.")
    company = company_of(company)
    sql = f"""SELECT TOP (?) v.ACCOUNTNUM, p.NAME, v.VENDGROUP, v.CURRENCY,
                     v.DATAAREAID AS COMPANY
              FROM VENDTABLE v {NAME_JOIN.format(t='v')}
              WHERE (p.NAME LIKE ? OR v.ACCOUNTNUM LIKE ?)"""
    params = [limit_of(limit, 50), f"%{text}%", f"%{text}%"]
    if company != "ALL":
        sql += " AND v.DATAAREAID = ?"
        params.append(company)
    rows = query(sql + " ORDER BY p.NAME", params)
    where = "any company" if company == "ALL" else company
    if not rows:
        return ask(f"No vendor found for '{text}' in {where}. Check spelling or search all companies?")
    if len(rows) > 1:
        return ask(f"{len(rows)} vendors match '{text}' in {where}. Which one?",
                   [f"{r['ACCOUNTNUM']} - {r['NAME']} ({r['COMPANY']})" for r in rows])
    return ok(rows[0])


@mcp.tool()
@ax_tool
def vendor_balance(vendor_account: str = "", company: str = "", top: int = 20) -> dict:
    """Vendor balance report (payables) from open vendor transactions.
    With vendor_account: that vendor's open balance. Without: top vendors we owe.
    In AX, vendor balances we owe are usually negative."""
    company = company_of(company)
    sql = f"""SELECT TOP (?) t.ACCOUNTNUM, p.NAME,
                     SUM(t.AMOUNTMST - t.SETTLEAMOUNTMST) AS BALANCE,
                     COUNT(*) AS OPEN_TRANSACTIONS
              FROM VENDTRANS t
              JOIN VENDTABLE v ON v.ACCOUNTNUM = t.ACCOUNTNUM AND v.DATAAREAID = t.DATAAREAID
              {NAME_JOIN.format(t='v')}
              WHERE t.DATAAREAID = ? AND t.AMOUNTMST <> t.SETTLEAMOUNTMST"""
    params = [limit_of(top, 100), company]
    if vendor_account:
        sql += " AND t.ACCOUNTNUM = ?"
        params.append(vendor_account.strip())
    rows = query(sql + " GROUP BY t.ACCOUNTNUM, p.NAME ORDER BY BALANCE ASC", params)
    if not rows:
        return ok([], message=f"No open vendor balances found in {company}.")
    return ok(rows, company=company, total_balance=round(sum(r["BALANCE"] for r in rows), 2))


@mcp.tool()
@ax_tool
def list_purchase_orders(vendor_account: str = "", status: str = "",
                         company: str = "", limit: int = 20) -> dict:
    """List the latest purchase orders with their amount.
    Filter by vendor_account and/or status: 'Open order', 'Received', 'Invoiced', 'Canceled'."""
    company = company_of(company)
    sql = """SELECT TOP (?) t.PURCHID, t.ORDERACCOUNT AS VENDOR, t.PURCHNAME, t.PURCHSTATUS,
                    t.CREATEDDATETIME AS CREATED,
                    (SELECT SUM(l.LINEAMOUNT) FROM PURCHLINE l
                     WHERE l.PURCHID = t.PURCHID AND l.DATAAREAID = t.DATAAREAID) AS AMOUNT
             FROM PURCHTABLE t WHERE t.DATAAREAID = ?"""
    params = [limit_of(limit, 100), company]
    if vendor_account:
        sql += " AND t.ORDERACCOUNT = ?"
        params.append(vendor_account.strip())
    status_num = find_status(status, PURCH_STATUS)
    if status_num is not None:
        sql += " AND t.PURCHSTATUS = ?"
        params.append(status_num)
    rows = query(sql + " ORDER BY t.CREATEDDATETIME DESC", params)
    for r in rows:
        r["STATUS"] = PURCH_STATUS.get(r.pop("PURCHSTATUS"), "Unknown")
        r["AMOUNT"] = r["AMOUNT"] or 0.0
    if not rows:
        return ok([], message=f"No matching purchase orders in {company}.")
    return ok(rows, company=company, count=len(rows),
              total_amount=round(sum(r["AMOUNT"] for r in rows), 2))



@mcp.tool()
@ax_tool
def search_items(text: str, company: str = "", limit: int = 20) -> dict:
    """Find items/products by part of the item number, name or search name."""
    text = text.strip()
    if len(text) < 2:
        return ask("Which item? Give at least 2 letters of the item number or name.")
    company = company_of(company)
    rows = query("""SELECT TOP (?) i.ITEMID, n.NAME, i.NAMEALIAS
                    FROM INVENTTABLE i
                    OUTER APPLY (SELECT TOP 1 tr.NAME FROM ECORESPRODUCTTRANSLATION tr
                                 WHERE tr.PRODUCT = i.PRODUCT) n
                    WHERE i.DATAAREAID = ?
                      AND (i.ITEMID LIKE ? OR i.NAMEALIAS LIKE ? OR n.NAME LIKE ?)
                    ORDER BY i.ITEMID""",
                 [limit_of(limit, 50), company, f"%{text}%", f"%{text}%", f"%{text}%"])
    if not rows:
        return ask(f"No item found for '{text}' in {company}. Try another name or company?")
    return ok(rows, company=company, count=len(rows))


@mcp.tool()
@ax_tool
def item_on_hand(item_id: str, warehouse: str = "", company: str = "") -> dict:
    """Stock ON-HAND for one item per warehouse: physical, reserved, available,
    ordered (incoming) and on order (outgoing). Optional warehouse = InventLocationId.
    If you only know the item name, call search_items first."""
    company = company_of(company)
    physical = "(s.POSTEDQTY + s.RECEIVED - s.DEDUCTED + s.REGISTERED - s.PICKED)"
    sql = f"""SELECT d.INVENTLOCATIONID AS WAREHOUSE,
                     SUM({physical}) AS PHYSICAL,
                     SUM(s.RESERVPHYSICAL) AS RESERVED,
                     SUM({physical} - s.RESERVPHYSICAL) AS AVAILABLE,
                     SUM(s.ORDERED) AS INCOMING_ORDERED,
                     SUM(s.ONORDER) AS OUTGOING_ON_ORDER
              FROM INVENTSUM s
              JOIN INVENTDIM d ON d.INVENTDIMID = s.INVENTDIMID AND d.DATAAREAID = s.DATAAREAID
              WHERE s.DATAAREAID = ? AND s.ITEMID = ? AND s.CLOSED = 0"""
    params = [company, item_id.strip()]
    if warehouse:
        sql += " AND d.INVENTLOCATIONID = ?"
        params.append(warehouse.strip())
    sql += f""" GROUP BY d.INVENTLOCATIONID
               HAVING SUM({physical}) <> 0 OR SUM(s.ORDERED) <> 0 OR SUM(s.ONORDER) <> 0
               ORDER BY d.INVENTLOCATIONID"""
    rows = query(sql, params)
    if not rows:
        return ok([], message=f"No stock or open quantities for item {item_id} in {company}.")
    return ok(rows, item_id=item_id, company=company,
              total_available=round(sum(r["AVAILABLE"] for r in rows), 2))




@mcp.tool()
@ax_tool
def find_tables(keyword: str, limit: int = 30) -> dict:
    """Find AX tables whose name contains a keyword (e.g. 'LEDGER', 'PROD', 'HCM', 'BOM').
    Use when no report tool fits, then describe_table, then run_sql_query."""
    if len(keyword.strip()) < 3:
        return ask("Give at least 3 letters of the table name, e.g. LEDGER, PROD, INVENT.")
    rows = query("""SELECT TOP (?) TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
                    WHERE TABLE_TYPE = 'BASE TABLE' AND TABLE_NAME LIKE ?
                    ORDER BY TABLE_NAME""", [limit_of(limit, 100), f"%{keyword.strip()}%"])
    return ok([r["TABLE_NAME"] for r in rows], count=len(rows))


@mcp.tool()
@ax_tool
def describe_table(table_name: str) -> dict:
    """Show the columns and data types of one AX table, before writing a query on it."""
    rows = query("""SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_NAME = ? ORDER BY ORDINAL_POSITION""", [table_name.strip().upper()])
    if not rows:
        return ask(f"No table called '{table_name}'. Use find_tables to search for the right name?")
    return ok(rows, table=table_name.upper(), column_count=len(rows),
              hint="Most AX tables are per company: filter on DATAAREAID. "
                   "Enums are stored as numbers.")


@mcp.tool()
@ax_tool
def run_sql_query(sql: str, max_rows: int = 100) -> dict:
    """Run a READ-ONLY SQL SELECT on the AX database for questions the report tools
    don't cover. Rules: one SELECT only, always filter by DATAAREAID, use TOP or
    GROUP BY to keep results small, and check column names with describe_table first.
    Inserts, updates, deletes and other changes are blocked."""
    try:
        clean = check_select_only(sql)
    except ValueError as e:
        return {"status": "blocked", "message": str(e)}
    max_rows = limit_of(max_rows, 500)
    rows = query(clean, max_rows=max_rows + 1)
    truncated = len(rows) > max_rows
    return ok(rows[:max_rows], row_count=min(len(rows), max_rows), truncated=truncated,
              note="More rows exist - add filters or GROUP BY." if truncated else "")


if __name__ == "__main__":
    mcp.run()
