"""테스트는 OS 언어와 무관하게 같은 결과가 나와야 하므로 기본 언어를 고정한다.
언어별 동작은 test_i18n.py 에서 명시적으로 바꿔 가며 검사한다."""
from se650.i18n import set_locale

set_locale("en")
