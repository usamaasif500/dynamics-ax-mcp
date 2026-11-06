
import asyncio, os, sys
from pathlib import Path
from mcp import ClientSession, StdioServerParameters, stdio_client

SERVER = str(Path(__file__).parent / "my-mcp.py")


CUSTOMER = os.getenv("TEST_CUSTOMER", "US-0001")
SALES_ID = os.getenv("TEST_SALES_ID", "SO-000123")
ITEM_TEXT = os.getenv("TEST_ITEM_TEXT", "seed")
COMPANY = os.getenv("AX_COMPANY", "DAT")

TESTS = [
    ("connection_status", {}),
    ("customer_balance", {"top": 5}),
    ("customer_balance", {"customer_account": CUSTOMER}),
    ("sales_summary", {"group_by": "month", "from_date": "2026-01-01"}),
    ("sales_summary", {"group_by": "customer", "top": 5}),
    ("sales_summary", {"group_by": "item", "top": 5}),
    ("sales_order_lines", {"sales_id": SALES_ID}),
    ("vendor_balance", {"top": 5}),
    ("list_purchase_orders", {"limit": 5}),
    ("search_items", {"text": ITEM_TEXT}),
    ("find_tables", {"keyword": "LEDGERJOURNAL"}),
    ("run_sql_query", {"sql": f"SELECT TOP 3 ACCOUNTNUM FROM CUSTTABLE WHERE DATAAREAID='{COMPANY}'"}),
    ("run_sql_query", {"sql": "DELETE FROM CUSTTABLE"}),  # expected: blocked
]

async def main():
    server = StdioServerParameters(command=sys.executable, args=[SERVER], env=dict(os.environ))
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(len(tools.tools), "tools:", [t.name for t in tools.tools])
            for name, args in TESTS:
                result = await session.call_tool(name, args)
                print(f"\n>> {name} {args}\n{result.content[0].text[:600]}")

asyncio.run(main())
