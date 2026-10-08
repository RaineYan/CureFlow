"""临时脚本：验证 sample_company.csv 的聚合值，给测试和文档用。"""
import pandas as pd

df = pd.read_csv("data/sample_company.csv")
print("rows:", len(df))
print("columns:", list(df.columns))
print()
agg = df.groupby("year")[["revenue", "cost", "users", "orders", "visits"]].sum()
print("annual sums:")
print(agg)
print()

rev25 = agg.loc[2025, "revenue"]
rev24 = agg.loc[2024, "revenue"]
cost25 = agg.loc[2025, "cost"]
users25 = agg.loc[2025, "users"]
users24 = agg.loc[2024, "users"]
orders25 = agg.loc[2025, "orders"]
orders24 = agg.loc[2024, "orders"]
visits25 = agg.loc[2025, "visits"]

print("metric values:")
print(f"  revenue_growth    = ({rev25} - {rev24}) / {rev24} = {(rev25 - rev24) / rev24}")
print(f"  profit_margin     = ({rev25} - {cost25}) / {rev25} = {(rev25 - cost25) / rev25}")
print(f"  cost_ratio        = {cost25} / {rev25} = {cost25 / rev25}")
print(f"  user_growth       = ({users25} - {users24}) / {users24} = {(users25 - users24) / users24}")
print(f"  conversion_rate   = {orders25} / {visits25} = {orders25 / visits25}")
print(f"  order_growth      = ({orders25} - {orders24}) / {orders24} = {(orders25 - orders24) / orders24}")
