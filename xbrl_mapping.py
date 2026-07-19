"""XBRL 표준계정 매핑 — 비율 계산에 필요한 원천 계정 후보 정의
같은 개념(예: 매출채권)도 회사마다 다른 태그를 쓰는 경우가 있어(비표준 확장 태그 포함)
후보를 우선순위 순으로 나열하고 첫 매치를 채택하는 fallback 방식을 쓴다.
"""

# {필드명: [(sj_div, account_id), ...]}  — 리스트 순서 = 우선순위
ACCOUNT_CANDIDATES = {
    "revenue": [
        ("IS", "ifrs-full_Revenue"),
        ("CIS", "ifrs-full_Revenue"),
    ],
    "cogs": [
        ("IS", "ifrs-full_CostOfSales"),
        ("CIS", "ifrs-full_CostOfSales"),
    ],
    "operating_income": [
        ("IS", "dart_OperatingIncomeLoss"),
        ("CIS", "dart_OperatingIncomeLoss"),
    ],
    "sga": [
        ("IS", "dart_TotalSellingGeneralAdministrativeExpenses"),
        ("CIS", "dart_TotalSellingGeneralAdministrativeExpenses"),
        ("IS", "ifrs-full_SellingGeneralAndAdministrativeExpense"),
        ("CIS", "ifrs-full_SellingGeneralAndAdministrativeExpense"),
    ],
    "interest_expense": [
        ("IS", "ifrs-full_FinanceCosts"),
        ("CIS", "ifrs-full_FinanceCosts"),
    ],
    "net_income": [
        ("IS", "ifrs-full_ProfitLoss"),
        ("CIS", "ifrs-full_ProfitLoss"),
    ],
    # 순수 매출채권(협의) 태그를 최우선으로 한다. ifrs-full_TradeAndOtherCurrentReceivables는
    # 매출채권+기타채권(미수금 등)을 합친 광의 개념이라 receivables_turnover의 peer 비교가능성을
    # 해친다 — LIMITATIONS.md §15 참고(동시 공시 396개사 비교 시 중위수 84% 차이 실측).
    "trade_receivables": [
        ("BS", "dart_ShortTermTradeReceivable"),
        ("BS", "ifrs-full_CurrentTradeReceivables"),
        ("BS", "ifrs-full_TradeReceivables"),
        ("BS", "ifrs-full_TradeAndOtherReceivables"),
        ("BS", "ifrs-full_TradeAndOtherCurrentReceivables"),
    ],
    "inventory": [
        ("BS", "ifrs-full_Inventories"),
    ],
    "total_assets": [
        ("BS", "ifrs-full_Assets"),
    ],
    "total_liabilities": [
        ("BS", "ifrs-full_Liabilities"),
    ],
    "total_equity": [
        ("BS", "ifrs-full_Equity"),
    ],
    "operating_cf": [
        ("CF", "ifrs-full_CashFlowsFromUsedInOperatingActivities"),
    ],
}
