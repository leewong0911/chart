# -*- coding: utf-8 -*-
"""QPS 页面骨架 + 通用 QPS 查询接口"""
from datetime import datetime, timedelta

from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from app1.models import QpsRecord

METRICS = ['all', 'select', 'insert', 'update', 'replace']
DEFAULT_WINDOW = timedelta(hours=1)
FALLBACK_STEP = timedelta(hours=1)
MAX_RANGE = timedelta(days=7)
# 聚合粒度映射表：按“查询跨度”分档，(跨度上限, 粒度分钟数, 粒度标签)
# 想让某个跨度更稀疏，把对应的分钟数调大即可
INTERVAL_RULES = [
    (timedelta(hours=1), 1, '1m'),
    (timedelta(hours=6), 2, '2m'),
    (timedelta(hours=12), 5, '5m'),
    (timedelta(days=1), 10, '10m'),
    (timedelta(days=3), 30, '30m'),
    (timedelta(days=7), 120, '2h'),
]
MAX_BUCKETS = 1440
DATE_ONLY = '%Y-%m-%d'
DATETIME_FORMATS = ('%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S', DATE_ONLY)


def qps(request):
    """只返回页面骨架，数据由前端异步拉 /api/qps/"""
    return render(request, 'qps.html')


def api_qps(request):
    """通用接口：/api/qps/?start=&end=&metric=

    start / end 支持 'YYYY-MM-DD'、'YYYY-MM-DD HH:MM'、'YYYY-MM-DD HH:MM:SS'
    只给一侧时，另一侧按 FALLBACK_STEP 外扩 1 小时；都不给则取最近 1 小时
    metric 支持逗号分隔（如 select,insert），不给则返回全部指标
    单次查询跨度最大 MAX_RANGE（7 天）
    """
    start, err = _parse_bound(request.GET.get('start'), is_end=False)
    if err:
        return _bad_request(err)

    end, err = _parse_bound(request.GET.get('end'), is_end=True)
    if err:
        return _bad_request(err)

    if start is None and end is None:
        start, end = _default_range()
    elif start is None:
        start = end - FALLBACK_STEP
    elif end is None:
        end = start + FALLBACK_STEP

    if start > end:
        return _bad_request('start 不能晚于 end')
    if end - start > MAX_RANGE:
        return _bad_request('时间跨度最大支持 {} 天，请缩小区间'.format(MAX_RANGE.days))

    metrics, err = _parse_metrics(request.GET.get('metric'))
    if err:
        return _bad_request(err)

    records = list(
        QpsRecord.objects.filter(ts__gte=start, ts__lte=end, metric__in=metrics)
        .values('ts', 'metric', 'value')
        .order_by('ts')
    )
    buckets, series, meta = _build_series(records, metrics, (start, end))

    return JsonResponse({
        'start': _display(start),
        'end': _display(end),
        'metrics': metrics,
        'total': len(records),
        'maxRangeDays': MAX_RANGE.days,
        'times': buckets,
        'series': series,
        'meta': meta,
    })


def _bad_request(message):
    return JsonResponse({'error': message}, status=400)


def _default_range():
    """默认看最近 DEFAULT_WINDOW（1 小时）"""
    now = timezone.localtime(timezone.now()).replace(second=0, microsecond=0)
    return now - DEFAULT_WINDOW, now


def _parse_bound(raw, is_end):
    """返回 (aware datetime 或 None, 错误信息或 None)"""
    if not raw:
        return None, None

    text = raw.strip().replace('+', ' ')
    for fmt in DATETIME_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if fmt == DATE_ONLY and is_end:
            parsed = parsed + timedelta(days=1) - timedelta(minutes=1)
        return timezone.make_aware(parsed), None
    return None, '时间格式无法解析：{}，支持 YYYY-MM-DD 或 YYYY-MM-DD HH:MM'.format(raw)


def _parse_metrics(raw):
    if not raw:
        return list(METRICS), None

    wanted = [item.strip() for item in raw.split(',') if item.strip()]
    if not wanted:
        return list(METRICS), None

    unknown = [item for item in wanted if item not in METRICS]
    if unknown:
        return None, '未知指标：{}，可选值 {}'.format(
            ', '.join(unknown), ', '.join(METRICS))
    return [m for m in METRICS if m in dict.fromkeys(wanted)], None


def _build_series(records, metrics, window):
    """按查询跨度选粒度聚合：每桶取该指标最大值，空桶补 0

    时间轴覆盖整个请求区间（而不是只覆盖有数据的首尾），这样切换预设时
    x 轴范围与右上角选择器一致。粒度由 _choose_step(跨度) 查表得出。
    返回 (时间标签列表, {指标: 值序列}, 粒度信息)
    粒度信息里的 base + stepSec 足以让前端还原每个点的完整时间戳
    """
    if not records:
        return [], {m: [] for m in metrics}, None

    start, end = window
    step_min, step_label = _choose_step(end - start)
    step_sec = step_min * 60
    base = _align(start, step_min)
    count = min(int((end - base).total_seconds() // step_sec) + 1, MAX_BUCKETS)

    stamps = [timezone.localtime(r['ts']).replace(second=0, microsecond=0) for r in records]
    series = {m: [0] * count for m in metrics}
    for record, stamp in zip(records, stamps):
        metric = record['metric']
        if metric not in series:
            continue
        idx = int((stamp - base).total_seconds() // step_sec)
        if 0 <= idx < count and record['value'] > series[metric][idx]:
            series[metric][idx] = record['value']

    meta = {
        'interval': step_label,
        'points': count,
        'base': base.strftime('%Y-%m-%d %H:%M:%S'),
        'stepSec': step_sec,
    }
    return _labels(base, count, step_min), series, meta


def _choose_step(span):
    """查询跨度落在 INTERVAL_RULES 哪一档，超出最后一档则用最后一档"""
    for limit, step_min, label in INTERVAL_RULES:
        if span <= limit:
            return step_min, label
    return INTERVAL_RULES[-1][1], INTERVAL_RULES[-1][2]


def _align(dt, step_min):
    """把时间向下对齐到粒度起点，保证整点/整刻切齐"""
    if step_min >= 1440:
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if step_min >= 60:
        hours = step_min // 60
        return dt.replace(hour=(dt.hour // hours) * hours, minute=0, second=0, microsecond=0)
    return dt.replace(minute=(dt.minute // step_min) * step_min, second=0, microsecond=0)


def _labels(base, count, step_min):
    """生成时间标签，跨天或按天粒度时带上月日"""
    last = base + timedelta(minutes=step_min * (count - 1))
    if step_min >= 1440:
        fmt = '%m-%d'
    elif base.date() != last.date():
        fmt = '%m-%d %H:%M'
    else:
        fmt = '%H:%M'
    return [(base + timedelta(minutes=step_min * i)).strftime(fmt) for i in range(count)]


def _display(dt):
    return timezone.localtime(dt).strftime('%Y-%m-%d %H:%M:%S')
