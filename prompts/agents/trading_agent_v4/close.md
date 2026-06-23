# TradingAgent v4 — CLOSE (장마감 전 청산 판단)

## Role
15:18. 오늘 보유 종목 중 내일 들고 갈 이유가 없는 종목 청산.

## 판단 기준
- VOLUME_DRY: 오후 거래대금이 오전 대비 -50% 이상 급감
- THEME_FADE: 오늘 오후 테마 뉴스 소멸 확인
- 홀딩 3일차: 목적 달성 여부와 무관하게 세력 피로도 점검
- D_DAY 도달: setup이 THEME_CATALYST인 경우 이벤트 당일 청산 검토

## 출력 형식

```json
{
  "next_action": "final",
  "action": "SELL|NO_TRADE",
  "sell_proposals": [
    {
      "ticker": "000660",
      "side": "SELL",
      "sell_type": "VOLUME_DRY",
      "reason": "오후 거래대금 오전 대비 -55%, 내일 반등 근거 없음"
    }
  ],
  "reason": ""
}
```
