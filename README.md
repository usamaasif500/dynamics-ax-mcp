# Dynamics AX 2012 MCP Server

Talk to your **Microsoft Dynamics AX 2012** ERP in plain English from **Claude, ChatGPT, Mistral Le Chat or Gemini**, using the open [Model Context Protocol (MCP)](https://modelcontextprotocol.io).

> "Who are our top 10 debtors, and how much is over 90 days?"
> "Show monthly invoiced sales for 2026 as a chart."
> "What's the available stock of item X by warehouse?"

The AI picks the right tool, the server runs a safe, read-only query on AX, and the AI explains the result.

## Coverage

- **12 ready-made business tools** for the questions people ask every day: aging, sales trends, orders, vendors, stock
- **Plus the Explorer tools**, which give the AI read access to **every table in the AX database**. It finds the right tables, reads their columns and writes its own guarded `SELECT`
- So **every module** is covered: Finance, Sales, Procurement, Inventory, Production, Projects, HR, Fixed Assets, across **all companies (DATAAREAID)**

| Module | Example question |
|---|---|
| Finance | "Trial balance by main account for October" · "Unposted general journals" |
| Sales & AR | "Top debtors with 90+ days overdue" · "Monthly invoiced sales for 2026" |
| Procurement & AP | "Open POs not received after 30 days" · "Which vendors do we owe the most?" |
| Inventory | "Available stock of item X by warehouse" · "Items with no movement in 6 months" |
| Production | "Production orders started this week and their status" |
| HR | "Headcount by department" |
| Fixed assets | "Assets acquired this year and their book value" |
| Admin | "Which companies exist?" · "Which AX users logged in today?" |

---

## Why

ERP data is locked behind forms, reports and SQL. Managers wait on IT for simple questions, and every new report means new SSRS development.
This server gives any MCP-capable AI **secure, read-only access to the entire AX 2012 database**, with business logic built in for the common reports, so anyone can ask questions in natural language and get answers in seconds.

## Architecture

```
 Claude / ChatGPT / Le Chat / Gemini
              │  MCP (stdio locally, Streamable HTTP remotely)
              ▼
 ┌─────────────────────────────┐
 │  MCP server (Python)        │  my-mcp.py      15 tools
 │  - API-key auth (web mode)  │  http_server.py web mode + auth
 │  - read-only SQL guard      │  ax_db.py       DB access + safety
 └─────────────────────────────┘
              │  pyodbc, parameterised queries, read-only login
              ▼
   AX 2012 SQL Server (MicrosoftDynamicsAX)
```

## Tools (15)

| Area | Tool | What it does |
|---|---|---|
| Basics | `connection_status` | Is the live AX database reachable? |
| | `list_companies` | Legal entities (DATAAREAID) |
| Customers | `search_customers` | By name/account, one or all companies |
| | `customer_balance` | **Receivables aging**: not due, 1-30, 31-60, 61-90, 90+ |
| Sales | `list_sales_orders` | Latest orders with status, type and amount |
| | `sales_order_lines` | Items, qty, price, amount for one order |
| | `sales_summary` | **Invoiced sales by month / customer / item** for a date range |
| Purchasing | `search_vendors`, `vendor_balance`, `list_purchase_orders` | Suppliers, payables, POs |
| Inventory | `search_items`, `item_on_hand` | Item lookup; physical / reserved / available per warehouse |
| **Explorer** | `find_tables` | Search all AX tables by name (e.g. `PROD`, `LEDGER`, `HCM`) |
| | `describe_table` | Columns and data types of any table |
| | `run_sql_query` | Guarded read-only `SELECT` on **any** table, with joins, GROUP BY and CTEs |

**How the Explorer works:** for a question no report covers, the AI calls `find_tables("PROD")`, then `describe_table("PRODTABLE")`, then writes a query with `run_sql_query`. It is filtered by company and limited in size, and the guard blocks anything that is not a single SELECT.

### AX 2012 know-how built in
- Customer and vendor names come from `DIRPARTYTABLE` via `PARTY`, not from `CUSTTABLE`
- Order amounts are summed from `SALESLINE` / `PURCHLINE`
- Enums (`SalesStatus`, `SalesType`, `PurchStatus`) are translated to readable text
- On-hand is calculated from `INVENTSUM` + `INVENTDIM` (physical = posted + received − deducted + registered − picked)
- Aging is calculated from open `CUSTTRANS` (`AMOUNTMST − SETTLEAMOUNTMST`) against `DUEDATE`
- Every query is scoped by `DATAAREAID`

## Asks instead of guessing

When a request is ambiguous, tools return `need_clarification` with a question and options, so the AI asks the user instead of inventing data:

```json
{ "status": "need_clarification",
  "ask_user": "3 customers match 'Noor'. Which one?",
  "options": ["C-0001 - Al Noor Traders (DAT)", "C-0003 - Noor Textiles (DAT)"] }
```

## Security

| Layer | Protection |
|---|---|
| Database | Dedicated SQL login with `db_datareader` only |
| Connection | Opened with `readonly=True`, 30 s query timeout |
| Queries | Parameterised (`?`), so no SQL injection from user text |
| Free SQL tool | Single `SELECT`/`WITH` only; `INSERT/UPDATE/DELETE/DROP/EXEC/INTO`… blocked; max 500 rows |
| Web mode | Every request needs an API key (Bearer header or secret path), compared in constant time |
| Secrets | `.env` file, git-ignored |

Writes are deliberately **not** done through SQL: in AX 2012 that would bypass RecId allocation, number sequences, X++ business logic and the AOS cache. Writes are planned through AIF services instead.

## Setup

**1. Read-only SQL login** (run in SSMS on the AX database server)
```sql
CREATE LOGIN mcp_reader WITH PASSWORD = '<strong password>';
GO
USE MicrosoftDynamicsAX;
GO
CREATE USER mcp_reader FOR LOGIN mcp_reader;
ALTER ROLE db_datareader ADD MEMBER mcp_reader;
```

**2. Install**
```bash
pip install -r requirements.txt
cp .env.example .env      # then fill in your values
python test.py            # calls every tool once
```

**3a. Local: Claude Desktop** (`claude_desktop_config.json`)
```json
{ "mcpServers": { "dynamics-erp": {
    "command": "python", "args": ["C:\\path\\to\\my-mcp.py"] } } }
```

**3b. Remote: Claude.ai, ChatGPT, Le Chat, Gemini**
```bash
python http_server.py                 # listens on 127.0.0.1:8000
tailscale funnel --bg 8000            # or any HTTPS tunnel
```
| Client | URL | Auth |
|---|---|---|
| Claude.ai | `https://<host>/mcp` | No sign-in + header `Authorization: Bearer <key>` |
| Mistral Le Chat | `https://<host>/mcp` | Bearer token = `<key>` |
| ChatGPT (Developer mode) | `https://<host>/<key>/mcp` | No authentication |

> Only expose ERP data to the internet with your organisation's approval.

## Roadmap
- [ ] Demo mode with sample data
- [ ] Writes through AIF (create sales order with confirmation step)
- [ ] OAuth for ChatGPT
- [ ] D365 Finance & Operations backend via OData (same tool names)
- [ ] More reports: GL trial balance, production orders, item aging

## Tech
Python · MCP Python SDK · pyodbc · SQL Server · Uvicorn · Tailscale Funnel

## Author
**Usama**, Microsoft Dynamics AX 2012 / D365 F&O developer (X++, SQL, SSRS, Python)
