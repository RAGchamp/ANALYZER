"""Words a question and a report use for the same thing (INFO/ANALYZE-NON-FIN-DATA-PLAN.md §4).

Each group lists interchangeable phrases; a question naming one also searches
for the others ("CV" finds "commercial vehicle", and the other way round).
Keep the phrases lower case; multi-word phrases are matched as words.
"""

GROUPS = [
    ["commercial vehicle", "cv", "truck", "trucks", "hcv", "class 8"],
    ["passenger vehicle", "pv", "car", "cars", "suv"],
    ["electric vehicle", "ev", "evs", "e-mobility", "bev", "electrification"],
    ["united states", "us", "usa", "north america", "america"],
    ["european union", "europe", "eu"],
    ["capital expenditure", "capex", "capital spending"],
    ["earnings before interest", "ebitda", "operating profit", "operating margin"],
    ["return on capital employed", "roce", "return on capital"],
    ["tariff", "tariffs", "trade policy", "duties", "protectionism"],
    ["defence", "defense", "armed forces", "military"],
    ["aerospace", "aircraft", "aviation"],
    ["outlook", "guidance", "expected", "expects", "forecast", "future"],
    ["revenue", "revenues", "sales", "turnover", "top line"],
    ["profit", "profitability", "earnings", "margin", "margins"],
    ["export", "exports", "overseas", "international"],
    ["domestic", "india", "indian"],
    ["risk", "risks", "uncertainty", "uncertainties", "exposure", "threat"],
    ["climate", "emissions", "carbon", "ghg", "net zero", "decarbonisation", "decarbonization"],
    ["employees", "workforce", "headcount", "people", "human capital", "talent"],
    ["acquisition", "acquisitions", "acquired", "merger", "purchase of"],
    ["cash flow", "liquidity", "funding", "cash position"],
    ["debt", "borrowings", "leverage", "loans"],
    ["dividend", "dividends", "payout", "buyback", "buy-back"],
    ["premiums", "premium", "underwriting", "net premiums written"],
    ["catastrophe", "catastrophes", "cat losses", "natural disasters", "wildfire", "hurricane"],
    ["copper", "cu"], ["iron ore", "iron"], ["coal", "steelmaking coal", "metallurgical coal"],
    ["digital", "ai", "artificial intelligence", "automation", "technology"],
    ["auditor", "audit", "critical audit matter", "key audit matter"],
    ["governance", "board", "directors", "board of directors"],
    ["remuneration", "compensation", "pay", "salary", "incentives"],
    ["cybersecurity", "cyber", "information security", "data privacy"],
    ["corporate social responsibility", "csr", "social initiatives", "community"],
    ["environmental social and governance", "esg"],
    ["business responsibility", "brsr", "sustainability reporting"],
]

# question words -> section kinds whose passages get a small boost (plan §4)
KIND_BOOSTS = [
    ({"why", "driver", "drivers", "drove", "outlook", "strategy", "segment", "segments", "business", "market",
      "markets", "demand", "growth", "performance", "perform", "performed", "doing", "competition", "customers",
      "industry", "management", "commentary", "operations"},
     {"mdna", "letter", "strategy", "business", "highlights", "board_report"}),
    ({"risk", "risks", "exposure", "uncertainty", "threat", "threats", "vulnerable"}, {"risk"}),
    ({"governance", "board", "directors", "independent", "committee", "remuneration", "compensation", "pay"},
     {"governance", "remuneration"}),
    ({"sustainability", "esg", "climate", "emissions", "carbon", "social", "environment", "environmental"},
     {"sustainability"}),
    ({"audit", "auditor", "auditors", "opinion"}, {"auditor_report"}),
]

# words in a question that name a report section ("what does the MD&A say …") -> section kinds / titles
SECTION_NAMES = [
    (r"\bmd\s?&\s?a\b|management'?s? discussion|operating and financial review", "mdna"),
    (r"board'?s'? report|directors'? report", "board_report"),
    (r"risk factors?", "risk"),
    (r"chairman'?s?|\bcmd\b|ceo'?s? (?:letter|message|review)|chair'?s review|letter to shareholders", "letter"),
    (r"corporate governance", "governance"),
    (r"brsr|business responsibility|sustainability report", "sustainability"),
    (r"auditor'?s'? report|critical audit matter|key audit matter", "auditor_report"),
]
