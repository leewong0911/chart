# -*- coding: utf-8 -*-
"""灌入 QPS 模拟数据，供 /qps/ 页面从数据库读取

两种模式：
  python manage.py seed_qps                单天尖峰（复刻最初截图那段曲线）
  python manage.py seed_qps --days 7       最近 7 天随机数据，用于验证自适应降采样
"""
import random
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from app1.models import QpsRecord

METRICS = ['all', 'select', 'insert', 'update', 'replace']

# 与最初截图对应的尖峰位置
SPIKES = {
    '08:19': 0.2, '08:23': 0.1, '08:42': 0.2, '08:48': 0.2,
    '08:53': 0.2, '09:00': 0.2, '09:19': 0.1,
}

START = '08:18'
END = '09:20'
MAX_ROWS = 1000000
# SQLite 单条语句变量数上限 999，每行 3 个字段，故批量不能超
BATCH_SIZE = 200


class Command(BaseCommand):
    help = '灌入 QPS 模拟数据：默认单天尖峰，给 --days 则灌最近 N 天随机数据'

    def add_arguments(self, parser):
        parser.add_argument('--day', default='2026-10-27', help='单天尖峰模式的日期 YYYY-MM-DD')
        parser.add_argument('--days', type=int, help='随机模式的天数，不给则为单天尖峰模式')
        parser.add_argument('--every', type=int, default=1, help='采样间隔（分钟），默认 1')
        parser.add_argument('--seed', type=int, default=42, help='随机种子，默认 42')
        parser.add_argument('--spike-rate', type=float, default=0.25,
                            help='随机模式下 select 出现尖峰的分钟占比，默认 0.25')
        parser.add_argument('--until', default='now',
                            help="随机模式的数据结束时刻，'now' 或 YYYY-MM-DD HH:MM")

    def handle(self, *args, **opts):
        if opts['days'] is None:
            return self._seed_spikes(opts)
        return self._seed_bulk(opts)

    # ---------- 单天尖峰模式 ----------
    @transaction.atomic
    def _seed_spikes(self, opts):
        day = datetime.strptime(opts['day'], '%Y-%m-%d').date()
        points = self._buckets(START, END)

        deleted, _ = QpsRecord.objects.filter(ts__date=day).delete()
        rows = []
        for hm in points:
            for metric in METRICS:
                rows.append(QpsRecord(
                    ts=self._aware(day, hm),
                    metric=metric,
                    value=SPIKES.get(hm, 0) if metric == 'select' else 0,
                ))
        QpsRecord.objects.bulk_create(rows)

        self.stdout.write(self.style.SUCCESS(
            u'清除 {} 条旧数据，写入 {} 条（{} 时间点 x {} 指标）'.format(
                deleted, len(rows), len(points), len(METRICS))))

    # ---------- 多天随机模式 ----------
    @transaction.atomic
    def _seed_bulk(self, opts):
        stop = self._resolve_until(opts['until'])
        every = max(1, opts['every'])
        days = opts['days']
        if days < 1:
            raise CommandError(u'--days 至少为 1')
        start = stop - timedelta(days=days)

        total_min = int((stop - start).total_seconds() // 60)
        estimated = (total_min // every + 1) * len(METRICS)
        if estimated > MAX_ROWS:
            raise CommandError(
                u'预计写入 {} 条，超过上限 {} 条，请加大 --every 或减小 --days'.format(
                    estimated, MAX_ROWS))

        deleted, _ = QpsRecord.objects.filter(ts__gte=start, ts__lt=stop).delete()

        # 只有 select 有尖峰，其余指标恒为 0，图形才不至于糊成一片
        rng = random.Random(opts['seed'])
        rate = opts['spike_rate']
        rows = []
        for offset in range(0, total_min, every):
            ts = start + timedelta(minutes=offset)
            for metric in METRICS:
                if metric == 'select' and rng.random() < rate:
                    value = round(rng.uniform(0.05, 1.4), 4)
                else:
                    value = 0
                rows.append(QpsRecord(ts=ts, metric=metric, value=value))
        QpsRecord.objects.bulk_create(rows, batch_size=BATCH_SIZE)

        self.stdout.write(self.style.SUCCESS(
            u'清除 {} 条旧数据，写入 {} 条（{} ~ {}，每 {} 分钟 x {} 指标）'.format(
                deleted, len(rows),
                self._plain(start), self._plain(stop), every, len(METRICS))))
        self.stdout.write(u'可访问以下地址观察粒度映射表效果：')
        for label, delta in [('最近 1 小时', timedelta(hours=1)),
                             ('最近 6 小时', timedelta(hours=6)),
                             ('最近 1 天', timedelta(days=1)),
                             ('最近 3 天', timedelta(days=3)),
                             ('最近 7 天', timedelta(days=min(days, 7)))]:
            self.stdout.write(u'  {}：/qps/?start={}&end={}'.format(
                label, self._param(stop - delta), self._param(stop)))
        self.stdout.write(u'  超过 7 天应被拒绝：/api/qps/?start={}&end={}'.format(
            self._param(stop - timedelta(days=8)), self._param(stop)))

    # ---------- 工具 ----------
    @staticmethod
    def _resolve_until(raw):
        now = timezone.localtime(timezone.now()).replace(second=0, microsecond=0)
        if not raw or raw == 'now':
            return now
        try:
            return timezone.make_aware(datetime.strptime(raw, '%Y-%m-%d %H:%M'))
        except ValueError:
            raise CommandError(u"--until 应为 'now' 或 YYYY-MM-DD HH:MM")

    @staticmethod
    def _plain(dt):
        return dt.strftime('%Y-%m-%d %H:%M')

    @staticmethod
    def _param(dt):
        return dt.strftime('%Y-%m-%d+%H:%M')

    @staticmethod
    def _buckets(start, end):
        sh, sm = [int(x) for x in start.split(':')]
        eh, em = [int(x) for x in end.split(':')]
        return ['{:02d}:{:02d}'.format(c // 60, c % 60)
                for c in range(sh * 60 + sm, eh * 60 + em + 1)]

    @staticmethod
    def _aware(day, hm):
        t = datetime.strptime(hm, '%H:%M').time()
        return timezone.make_aware(datetime.combine(day, t))
