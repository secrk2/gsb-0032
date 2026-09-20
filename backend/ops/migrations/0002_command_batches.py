# 快捷命令执行：命令批次 + 作业输出落库 + 错误分类 + 危险命令留痕
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('ops', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='CommandBatch',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(default='快捷命令', max_length=255, verbose_name='批次名称')),
                ('command', models.TextField(verbose_name='命令/脚本')),
                ('actor', models.CharField(default='admin', max_length=64, verbose_name='操作人')),
                ('dangerous', models.BooleanField(default=False, verbose_name='命中危险命令')),
                ('confirm_reason', models.TextField(blank=True, default='', verbose_name='危险命令确认原因')),
                ('host_count', models.PositiveIntegerField(default=0, verbose_name='目标主机数')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
            ],
            options={
                'verbose_name': '命令批次',
                'verbose_name_plural': '命令批次',
                'ordering': ['-created_at'],
                'indexes': [models.Index(fields=['-created_at'], name='ops_command_created_8f474d_idx')],
            },
        ),
        migrations.AddField(
            model_name='job',
            name='batch',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='jobs', to='ops.commandbatch', verbose_name='所属命令批次'),
        ),
        migrations.AddField(
            model_name='job',
            name='actor',
            field=models.CharField(default='admin', max_length=64, verbose_name='操作人'),
        ),
        migrations.AddField(
            model_name='job',
            name='output',
            field=models.TextField(blank=True, default='', verbose_name='完整输出'),
        ),
        migrations.AddField(
            model_name='job',
            name='error_kind',
            field=models.CharField(blank=True, choices=[('timeout', '连接超时'), ('auth_failed', '认证失败'),
                                                        ('connection_refused', '连接被拒绝'),
                                                        ('host_unreachable', '主机不可达'),
                                                        ('connection_reset', '连接被重置'),
                                                        ('dns_error', '地址解析失败'),
                                                        ('no_credential', '未配置凭据'),
                                                        ('aborted', '用户中断'),
                                                        ('ssh_error', 'SSH 协议错误'),
                                                        ('worker_restart', '执行服务重启'),
                                                        ('error', '其他错误')],
                                   default='', max_length=32, verbose_name='错误分类'),
        ),
        migrations.AddField(
            model_name='job',
            name='error_message',
            field=models.TextField(blank=True, default='', verbose_name='错误详情'),
        ),
        migrations.AddField(
            model_name='job',
            name='dangerous',
            field=models.BooleanField(default=False, verbose_name='命中危险命令'),
        ),
        migrations.AddField(
            model_name='job',
            name='confirm_reason',
            field=models.TextField(blank=True, default='', verbose_name='危险命令确认原因'),
        ),
        migrations.AddIndex(
            model_name='job',
            index=models.Index(fields=['host', '-created_at'], name='ops_job_host_id_505ba0_idx'),
        ),
        migrations.AddIndex(
            model_name='job',
            index=models.Index(fields=['batch'], name='ops_job_batch_i_fcbc25_idx'),
        ),
        migrations.AlterField(
            model_name='job',
            name='status',
            field=models.CharField(
                choices=[('running', '执行中'), ('success', '成功'), ('failed', '失败'),
                         ('aborted', '已中断')],
                default='running', max_length=16, verbose_name='状态'),
        ),
    ]
