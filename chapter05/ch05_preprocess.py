"""Chapter 05 — 내 전처리 규칙을 명령 하나로 재현하는 스크립트.

실행 (강사 저장소 llm-data-analysis-course 루트에서):
    python <이 파일 경로> [출력 폴더]

입력: practice/chapter05/data/raw/*.csv (읽기만 함)
출력: 출력 폴더(기본 data/processed/ch05_notebook)에 *_clean.csv 4개 + change_log.csv
규칙: chapter05.ipynb 2~4번 셀과 같은 코드 조각을 그대로 사용 (Notebook 결과와 바이트 단위 비교로 검증)
"""
import hashlib
import sys
from pathlib import Path

import numpy as np
import pandas as pd


def find_project_root(start_path):
    start_path = Path(start_path).resolve()
    for candidate in [start_path, *start_path.parents]:
        if (candidate / 'requirements.txt').exists() and (candidate / 'scripts').exists():
            return candidate
    raise FileNotFoundError('강사 저장소(llm-data-analysis-course) 안에서 실행하세요.')


PROJECT_ROOT = find_project_root(Path.cwd())
RAW_DIR = PROJECT_ROOT / 'practice' / 'chapter05' / 'data' / 'raw'
OUT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT_ROOT / 'data' / 'processed' / 'ch05_notebook'
FILES = ['customers', 'products', 'orders', 'order_items']
KEYS = {'customers': 'customer_id', 'products': 'product_id', 'orders': 'order_id', 'order_items': 'order_item_id'}
raw_hash = {name: hashlib.md5((RAW_DIR / f'{name}.csv').read_bytes()).hexdigest() for name in FILES}
raw = {name: pd.read_csv(RAW_DIR / f'{name}.csv') for name in FILES}
change_log = []


def log(dataset, step, rule, affected):
    change_log.append({'dataset': dataset, 'step': step, 'rule': rule, 'affected': int(affected)})


# [규칙 2-1] 문자열 앞뒤 공백 제거, 빈 문자열 → 결측
def strip_strings(df, dataset):
    result = df.copy()
    for col in result.columns:
        if pd.api.types.is_string_dtype(result[col]) or result[col].dtype == object:
            before = result[col]
            after = before.where(before.isna(), before.astype(str).str.strip()).replace('', np.nan)
            changed = (before.astype(str) != after.astype(str)) & before.notna()
            if changed.sum():
                log(dataset, '2-문자열', f'{col} 앞뒤 공백 제거', changed.sum())
            result[col] = after
    return result


clean = {name: strip_strings(raw[name], name) for name in FILES}   # raw는 그대로 두고 복사본에서 처리


# [규칙 2-2] 숫자 변환: 쉼표 제거, quantity는 단위 접미사 '개'만 제거. 의미를 추측해야 하는 문자(three, unknown 등)는 NaN
def parse_number(series, suffix=None):
    text = series.astype(str).str.replace(',', '', regex=False)
    if suffix:
        text = text.str.replace(rf'{suffix}$', '', regex=True)
    return pd.to_numeric(text.where(series.notna()), errors='coerce')


num_rows = []
for name, col, suffix in [('customers', 'age', None), ('products', 'price', None),
                          ('order_items', 'quantity', '개'), ('order_items', 'unit_price', None)]:
    df = clean[name]
    original = df[col].copy()
    converted = parse_number(original, suffix)
    recovered = pd.to_numeric(original, errors='coerce').isna() & converted.notna() & original.notna()
    failed = original.notna() & converted.isna()
    df[col + '_raw'] = original.where(failed)          # 변환 실패 원문은 별도 컬럼에 보존
    df[col] = converted
    log(name, '2-숫자', f'{col} 형식 정리로 복구(쉼표/접미사)', recovered.sum())
    log(name, '2-숫자', f'{col} 변환 실패 → NaN', failed.sum())
    num_rows.append({'dataset': name, 'column': col, 'recovered': int(recovered.sum()),
                     'recovered_examples': sorted(original[recovered].astype(str).unique().tolist()),
                     'failed': int(failed.sum()), 'failed_values': sorted(original[failed].astype(str).unique().tolist()),
                     'missing_before': int(original.isna().sum()), 'nan_after': int(converted.isna().sum())})
number_conversion = pd.DataFrame(num_rows)


# [규칙 2-3] 날짜 변환: 형식을 YYYY-MM-DD로 고정, 실패는 NaT로 남김(임의 대체 안 함)
date_rows = []
for name, col in [('customers', 'signup_date'), ('orders', 'order_date')]:
    df = clean[name]
    original = df[col].copy()
    converted = pd.to_datetime(original, format='%Y-%m-%d', errors='coerce')
    failed = original.notna() & converted.isna()
    df[col + '_raw'] = original.where(failed)
    df[col] = converted
    log(name, '2-날짜', f'{col} 변환 실패 → NaT', failed.sum())
    date_rows.append({'dataset': name, 'column': col, 'converted': int(converted.notna().sum()), 'failed': int(failed.sum()),
                      'failed_values': sorted(original[failed].astype(str).unique().tolist()),
                      'min': converted.min().date(), 'max': converted.max().date()})
date_conversion = pd.DataFrame(date_rows)


# [규칙 2-4] 결측 처리: age는 중앙값 대체 + 플래그, city는 Unknown, 날짜·가격·수량·단가는 대체하지 않음
AGE_VALID_MAX = 120
c = clean['customers']
age_valid = c['age'].where(c['age'].between(0, AGE_VALID_MAX))
age_median = age_valid.median()                       # 범위 밖 값(150)은 중앙값 계산에서 제외
c['age_imputed'] = c['age'].isna()
c['age'] = c['age'].fillna(age_median)
log('customers', '2-결측', f'age 결측(원래 결측+변환 실패) → 중앙값 {age_median} 대체, age_imputed 표시', c['age_imputed'].sum())
city_na = c['city'].isna()
c['city'] = c['city'].fillna('Unknown')
log('customers', '2-결측', 'city 결측 → Unknown', city_na.sum())


# [규칙 3-1] 중복: 문자열·타입·결측 처리를 마친 뒤 전체 행 중복을 다시 계산 → 완전 중복만 제거
dup_rows = []
for name in FILES:
    df, key = clean[name], KEYS[name]
    full_dup = df.duplicated()
    after = df.drop_duplicates().reset_index(drop=True)
    dup_rows.append({'dataset': name, 'full_duplicate_rows': int(full_dup.sum()),
                     'removed_keys': sorted(df.loc[full_dup, key].tolist()),
                     'key_dup_before': int(df[key].duplicated().sum()), 'key_dup_after': int(after[key].duplicated().sum())})
    log(name, '3-중복', '완전 중복 행 제거', full_dup.sum())
    clean[name] = after
duplicate_check = pd.DataFrame(dup_rows)


# [규칙 3-2] 범주 표준화 + 허용값 검사
STATUS_MAP = {
    'complete': 'completed', 'Complete': 'completed', 'COMPLETED': 'completed', '완료': 'completed',
    'cancel': 'cancelled', 'Cancel': 'cancelled', 'CANCELLED': 'cancelled', '취소': 'cancelled',
    'refund': 'refunded', 'Refund': 'refunded', 'REFUNDED': 'refunded', '환불': 'refunded',
}
ALLOWED = {
    ('orders', 'order_status'): {'completed', 'cancelled', 'refunded'},
    ('orders', 'payment_method'): {'card', 'kakao_pay', 'naver_pay', 'bank_transfer'},
    ('products', 'category'): {'도서', '뷰티', '생활용품', '스포츠', '식품', '전자기기', '패션'},
    ('customers', 'city'): {'서울', '부산', '대구', '인천', '광주', '대전', '울산', '수원', '성남', '고양', 'Unknown'},
    ('customers', 'gender'): {'M', 'F'},
}


def outside(name, col):
    df = clean[name]
    return sorted(df.loc[~df[col].isin(ALLOWED[(name, col)]), col].astype(str).unique().tolist())


before_std = {k: outside(*k) for k in ALLOWED}
o = clean['orders']
status_changed = o['order_status'].isin(STATUS_MAP.keys())
o['order_status'] = o['order_status'].replace(STATUS_MAP)
log('orders', '3-범주', 'order_status 표기 통일', status_changed.sum())

p = clean['products']
cat_before = p['category'].copy()
p['category'] = p['category'].str.replace(r'\s+', '', regex=True)    # 내부 공백 제거('전자 기기' → '전자기기')
log('products', '3-범주', 'category 내부 공백 제거', (cat_before != p['category']).sum())

category_check = pd.DataFrame([
    {'column': f'{n}.{col}', 'outside_before': before_std[(n, col)], 'outside_after': outside(n, col)} for (n, col) in ALLOWED
])


# [규칙 3-3] 이상값 후보: 삭제·수정하지 않고 flag_* 컬럼으로 표시
AS_OF = pd.Timestamp('2026-10-15')     # 작성일. 이후 날짜는 '미래 주문일' 후보
c, p, o, oi = (clean[n] for n in FILES)
q1, q3 = oi['quantity'].quantile([0.25, 0.75])
q_upper = q3 + 1.5 * (q3 - q1)

c['flag_age_out_of_range'] = ~c['age'].between(0, AGE_VALID_MAX)
p['flag_price_invalid'] = p['price'].isna() | (p['price'] <= 0)
o['flag_date_invalid'] = o['order_date'].isna()
o['flag_future_date'] = o['order_date'] > AS_OF
oi['flag_qty_non_positive'] = oi['quantity'] <= 0
oi['flag_qty_large'] = oi['quantity'] > q_upper
oi['flag_price_non_positive'] = oi['unit_price'] <= 0
signup = o.merge(c[['customer_id', 'signup_date']], on='customer_id', how='left', validate='many_to_one')
o['flag_order_before_signup'] = (signup['order_date'] < signup['signup_date']).to_numpy()

outlier_rules = [
    ('customers', 'age > 120', 'flag_age_out_of_range', 'age', 'customer_id'),
    ('products', 'price 결측 또는 ≤ 0', 'flag_price_invalid', 'price', 'product_id'),
    ('orders', f'order_date > {AS_OF.date()} (미래)', 'flag_future_date', 'order_date', 'order_id'),
    ('orders', 'order_date < signup_date', 'flag_order_before_signup', None, 'order_id'),
    ('order_items', 'quantity ≤ 0', 'flag_qty_non_positive', 'quantity', 'order_item_id'),
    ('order_items', f'quantity > Q3+1.5·IQR ({q_upper:g})', 'flag_qty_large', 'quantity', 'order_item_id'),
    ('order_items', 'unit_price ≤ 0', 'flag_price_non_positive', 'unit_price', 'order_item_id'),
]
rows = []
for name, rule, flag, col, key in outlier_rules:
    df = clean[name]
    m = df[flag]
    ids = df.loc[m, key].tolist()
    vals = [] if col is None else [str(v.date()) if hasattr(v, 'date') else v for v in df.loc[m, col].tolist()]
    rows.append({'dataset': name, 'rule': rule, 'count': int(m.sum()),
                 'share(%)': round(m.mean() * 100, 1), 'values': vals[:6], 'ids': ids[:6] + (['…'] if len(ids) > 6 else [])})
    log(name, '3-이상값', f'후보 플래그: {rule}', m.sum())
outlier_candidates = pd.DataFrame(rows).assign(처리='삭제·수정 안 함, 플래그')


# [규칙 4-1] 파생 컬럼: 날짜·수량·단가가 유효한 행에서만 계산
o, oi = clean['orders'], clean['order_items']
o['order_month'] = o['order_date'].dt.strftime('%Y-%m')          # NaT → NaN (문자열 'NaT'가 생기지 않게)
o['order_dayofweek'] = o['order_date'].dt.day_name()
valid_line = (oi['quantity'] > 0) & (oi['unit_price'] > 0)
oi['is_valid_line'] = valid_line
oi['line_total'] = (oi['quantity'] * oi['unit_price']).where(valid_line)


# [규칙 4-2] PK/FK 재검증: 끊긴 관계는 삭제하지 않고 flag_fk_* 로 표시
fk_rows = []
for child, ck, parent, pk in [('orders', 'customer_id', 'customers', 'customer_id'),
                              ('order_items', 'order_id', 'orders', 'order_id'),
                              ('order_items', 'product_id', 'products', 'product_id')]:
    bad = ~clean[child][ck].isin(clean[parent][pk])
    clean[child][f'flag_fk_{ck}_missing'] = bad
    fk_rows.append({'check': f'{child}.{ck} → {parent}.{pk}', 'invalid_count': int(bad.sum()),
                    'missing_ids': sorted(clean[child].loc[bad, ck].unique().tolist()),
                    'child_rows': clean[child].loc[bad, KEYS[child]].tolist()})
    log(child, '4-관계', f'{ck} 참조 대상 없음 플래그', bad.sum())
fk_check = pd.DataFrame(fk_rows)
pk_check = pd.DataFrame([{'table': n, 'pk': KEYS[n], 'duplicated': int(df[KEYS[n]].duplicated().sum()),
                          'null': int(df[KEYS[n]].isna().sum())} for n, df in clean.items()])


# [저장 전 점검] 규칙 통과 여부 확인 — 실패하면 AssertionError로 중단되어 파일이 저장되지 않음
assert all(len(v) == 0 for v in category_check['outside_after']), '허용값 밖 범주 남음'
assert (pk_check['duplicated'] == 0).all() and (pk_check['null'] == 0).all(), 'PK 중복/결측'
assert (duplicate_check['key_dup_after'] == 0).all(), '중복 제거 후 key 중복 남음'
assert raw_hash == {name: hashlib.md5((RAW_DIR / f'{name}.csv').read_bytes()).hexdigest() for name in FILES}, 'raw 원본 변경됨'
print('checks: 허용값 밖 0 | PK 중복·결측 0 | raw 변경 없음 | FK 누락', dict(zip(fk_check['check'], fk_check['invalid_count'])))


# [저장] 개인정보(name) 제외 + 변경 기록 저장
change_log_df = pd.DataFrame(change_log)
change_log_df = change_log_df[change_log_df['affected'] > 0].reset_index(drop=True)
to_save = {name: df.drop(columns=['name'], errors='ignore') for name, df in clean.items()}
OUT_DIR.mkdir(parents=True, exist_ok=True)
for name, df in to_save.items():
    df.to_csv(OUT_DIR / f'{name}_clean.csv', index=False, encoding='utf-8-sig')
change_log_df.to_csv(OUT_DIR / 'change_log.csv', index=False, encoding='utf-8-sig')

print('saved:', OUT_DIR.relative_to(PROJECT_ROOT) if OUT_DIR.is_relative_to(PROJECT_ROOT) else OUT_DIR.name, '| rows:', {n: len(df) for n, df in to_save.items()}, '| change_log:', len(change_log_df))
