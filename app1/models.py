from django.db import models


class QpsRecord(models.Model):
    """QPS 采集点，模拟监控数据库里的一张明细表"""

    METRIC_CHOICES = [
        ('all', 'all'),
        ('select', 'select'),
        ('insert', 'insert'),
        ('update', 'update'),
        ('replace', 'replace'),
    ]

    ts = models.DateTimeField('采集时间')
    metric = models.CharField('指标', max_length=16, choices=METRIC_CHOICES)
    value = models.FloatField('值', default=0)

    class Meta:
        verbose_name = 'QPS采集点'
        verbose_name_plural = 'QPS采集点'
        ordering = ['ts']
        unique_together = (('ts', 'metric'),)
        indexes = [models.Index(fields=['metric', 'ts'], name='idx_qps_metric_ts')]

    def __str__(self):
        return '{} {} {}'.format(self.ts, self.metric, self.value)
