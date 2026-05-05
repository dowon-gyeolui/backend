"""자미두수(紫微斗數) — deterministic chart computation engine.

이 모듈은 LLM 추측이 아니라 표준 안성술(安星術) 알고리즘으로 12궁 ×
주성·부성·사화 차트를 결정론적으로 계산한다. 같은 사용자에게 같은 차트.

흐름:
  1. solar birth_date+time → lunar (KoreanLunarCalendar)
  2. 안명궁(寅起正月, 順月逆時)
  3. 12궁 배치 (역시계방향)
  4. 안궁간(五虎遁)
  5. 정오행국(納音五行 lookup)
  6. 안주성(자미부터 시작 → 14주성 deterministic offset)
  7. 안부성(좌보·우필·문창·문곡·천괴·천월·녹존·천마·경양·타라·화성·영성)
  8. 사화(年干 → 化祿/化權/化科/化忌)

공개 API: compute_chart(birth_date, birth_time, gender, ...) -> JamidusuChart
"""

from app.services.jamidusu.chart import compute_chart
from app.services.jamidusu.schema import (
    JamidusuChart,
    Palace,
    Star,
)

__all__ = ["compute_chart", "JamidusuChart", "Palace", "Star"]