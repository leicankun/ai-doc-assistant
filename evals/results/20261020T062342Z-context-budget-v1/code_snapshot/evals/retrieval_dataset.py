"""独立的 60 题检索专项集；不改写既有基线，所有资料均为虚构运维规则。"""
import hashlib
import json
from collections import Counter
from pathlib import Path

from evals.agentic_dataset import DOCUMENTS as ORIGINAL_DOCUMENTS

DATASET = Path(__file__).parent / 'retrieval_benchmark'


def section(title, text, redundancy=None):
    return {'title': title, 'text': text, 'redundancy_group': redundancy}


def documents():
    docs = {name: {'title': title, 'sections': [section(f'第 {i} 节', text)
            for i, text in enumerate(paragraphs, 1)]}
            for name, (title, paragraphs) in ORIGINAL_DOCUMENTS.items()}
    extra = {
        'orion-cache-v3.md': ('Orion 3.0 缓存运行手册', [
            section('连接参数', 'Orion 3.0 的缓存服务监听端口为 6388，连接超时为 4 秒；客户端需要同时设置这两个参数。端口不是应用 HTTP 端口，超时仅指建立连接的等待时间。'),
            section('缓存失效处理', 'Orion 3.0 缓存失效后先标记条目过期，再从主数据库重建缓存。重建时必须使用租户锁，禁止先清空整个缓存库。', 'orion-cache-rebuild'),
            section('租户锁', 'Orion 3.0 缓存重建锁按租户隔离，有效期为 45 秒。锁释放前核对持有者标识；不能删除另一个任务取得的锁。'),
            section('数据保留', 'Orion 3.0 缓存普通条目保留 20 分钟，空结果条目保留 30 秒。空结果使用独立过期策略，不能按普通条目的时间保留。'),
            section('刷新窗口', 'Orion 3.0 每 5 分钟扫描一次即将过期的缓存，仅刷新剩余有效期小于 60 秒的热点条目。扫描周期不是所有条目的有效期。'),
            section('缓存失效处理', 'Orion 3.0 缓存失效后先标记条目过期，再从主数据库重建缓存。重建时必须使用租户锁，禁止先清空整个缓存库。', 'orion-cache-rebuild'),
            section('回源保护', 'Orion 3.0 每个租户最多同时执行 8 个缓存回源任务。超过数量时请求进入有界队列，不能直接放大数据库连接池。'),
            section('队列边界', 'Orion 3.0 缓存回源队列最多保存 200 个任务，满队列返回 C429。该错误表示回源拥塞，不等同于访问令牌过期。'),
            section('健康检查', 'Orion 3.0 缓存就绪路径为 /cache/ready，检查连接和试读。应用进程存活不能代替缓存就绪检查。'),
            section('日志脱敏', 'Orion 3.0 缓存日志只记录键的 SHA-256 摘要，不记录完整键和值。排查时按请求标识关联应用日志。'),
            section('恢复权限', 'Orion 3.0 缓存恢复任务仅允许 cache-operator 角色执行；普通只读账号不能执行重建。授权针对操作而非部署机器。'),
        ]),
        'orion-worker-v3.md': ('Orion 3.0 任务工作进程', [
            section('工作线程', 'Orion 3.0 工作进程默认启动 4 个线程，单进程线程上限为 12。调整线程数后需要重启工作进程，不能只重启监控组件。'),
            section('任务重试', 'Orion 3.0 普通任务最多重试 3 次，每次间隔 12 秒。达到重试上限后进入失败队列，不得无限重试。'),
            section('幂等约束', 'Orion 3.0 每个任务携带唯一 job_id，幂等记录保留 48 小时。重复投递必须先检查该记录，不能仅比较任务正文。'),
            section('故障码', 'Orion 3.0 的 W301 表示失败队列存储不可达，先检查失败队列地址和权限。W301 不表示缓存连接失败。'),
            section('优雅停止', 'Orion 3.0 停止工作进程时先停止领取新任务，再等待在途任务完成，最长等待 90 秒。超时任务登记后重新投递。'),
            section('缓存重建排障', 'Orion 3.0 遇到过期缓存，应先置过期标记，持有租户锁后从主数据库重新生成条目，不能清空整个缓存库。', 'orion-cache-rebuild'),
            section('故障日志', 'Orion 3.0 工作进程日志保留 21 天，失败队列中的任务正文保留 72 小时。两种保留时间针对不同对象。'),
            section('Linux 热加载', 'Orion 3.0 在 Linux 生产环境支持热加载 worker.log_level；连接地址和线程数修改都必须重启。'),
            section('Windows 例外', 'Orion 3.0 Windows 开发环境不支持任何配置热加载，修改 worker.log_level 同样必须重启工作进程。'),
            section('归档输出', 'Orion 3.0 任务归档写入独立对象存储，归档账号只有新增权限。归档失败时不得删除原始失败任务。'),
            section('监控约定', 'Orion 3.0 每 10 秒上报一次任务队列长度和在途任务数，标签不能包含完整任务正文。监控不提供自动扩容保证。'),
        ]),
        'orion-archive-v1.md': ('Orion 1.1 历史手册', [
            section('历史连接参数', 'Orion 1.1 缓存端口为 6377，连接超时为 9 秒；这些参数不适用于 Orion 3.0。'),
            section('历史重建', 'Orion 1.1 缓存重建使用全局锁，有效期为 120 秒；升级到 3.0 后应改用租户锁，不得沿用旧锁粒度。'),
            section('历史线程', 'Orion 1.1 工作进程固定使用 2 个线程，不能热修改。该限制与新版可配置线程数不同。'),
            section('历史重试', 'Orion 1.1 普通任务最多重试 1 次，间隔 30 秒，不采用新版的重试策略。'),
            section('历史停止', 'Orion 1.1 停止进程前必须人工清空待执行队列；该做法不用于 3.0 的优雅停止流程。'),
            section('历史认证', 'Orion 1.1 使用机器级静态口令，不能把该口令直接当作新版角色凭证。'),
            section('升级迁移', 'Orion 1.1 升级前导出任务状态和幂等记录，检查失败任务列表后停止旧进程。不得同时运行两个版本的写入进程。'),
            section('历史日志', 'Orion 1.1 工作日志保留 7 天，日志读取权限需要单独申请。该时间不适用于新版日志。'),
            section('历史平台', 'Orion 1.1 只支持 Linux，所有配置修改均需重启；不能据此推断新版 Windows 的操作命令。'),
            section('承诺边界', 'Orion 历史和新版手册均没有公开商业价格、可用率承诺或缓存重建耗时承诺。不能从扫描周期推断恢复时限。'),
        ]),
        'aster-index-v4.md': ('Aster 4.2 索引运行手册', [
            section('向量维度', 'Aster 4.2 使用 768 维向量，文档与查询必须使用同一模型版本。维度相同并不代表来自不同模型的向量可以混用。'),
            section('入库失败', 'Aster 4.2 入库失败后保留原文件和已生成分块。先核对模型接口配置，再按现有分块标识重试，不创建另一份文档记录。', 'aster-index-retry'),
            section('索引版本', 'Aster 4.2 分块版本与入库版本相等才表示索引有效。重新分块后旧向量不能继续用于语义检索，必须完成新版本入库。', 'aster-index-version'),
            section('入库失败', 'Aster 4.2 入库失败后保留原文件和已生成分块。先核对模型接口配置，再按现有分块标识重试，不创建另一份文档记录。', 'aster-index-retry'),
            section('I401 处理', 'Aster 4.2 的 I401 表示模型接口鉴权失败。先检查模型服务密钥和访问地域，不得删除原文档来修复鉴权。'),
            section('I422 处理', 'Aster 4.2 的 I422 表示向量维度不匹配。应核对查询和文档的模型版本以及 768 维配置，不能用截断旧向量来补救。'),
            section('重复记录', 'Aster 4.2 使用 document_id 和 chunk_id 共同构成向量记录身份，重试时更新相同记录而不是追加新记录。', 'aster-vector-identity'),
            section('索引版本', 'Aster 4.2 分块版本与入库版本相等才表示索引有效。重新分块后旧向量不能继续用于语义检索，必须完成新版本入库。', 'aster-index-version'),
            section('分块边界', 'Aster 4.2 分块长度上限为 600 字符，重叠上限为 80 字符；优先沿标题和段落切分，不将页码当作字符偏移。'),
            section('索引备份', 'Aster 4.2 每天 02:00 备份索引清单和原文件校验值，保留 10 天；恢复时向量可以重建，但原文件必须校验。'),
            section('删除顺序', 'Aster 4.2 删除文档先撤销检索资格，再清理向量和原文件。清理失败时保留待清理状态，不能继续返回旧片段。'),
            section('批量窗口', 'Aster 4.2 每批最多提交 16 个分块生成向量，单批失败不能把整份文档标记为入库成功。'),
        ]),
        'aster-permissions-v4.md': ('Aster 4.2 权限与知识库范围', [
            section('知识库成员', 'Aster 4.2 检索仅允许访问当前知识库的成员文档；指定 document_ids 还要校验它们确实属于当前知识库。', 'aster-scope'),
            section('角色范围', 'Aster 4.2 reader 只能读取，contributor 可以上传但不能删除，owner 可以删除和管理成员。角色授权按知识库分别维护。'),
            section('租户边界', 'Aster 4.2 租户之间的文档身份互不共享，同名文档不能因为标题相同就被并入另一租户的检索范围。'),
            section('知识库成员', 'Aster 4.2 检索仅允许访问当前知识库的成员文档；指定 document_ids 还要校验它们确实属于当前知识库。', 'aster-scope'),
            section('成员移出', 'Aster 4.2 文档移出知识库后保留原文件和向量，但不能再从该知识库检索；其他仍有成员关联的知识库可以继续使用。'),
            section('权限缓存', 'Aster 4.2 成员权限缓存有效期为 15 秒，显式移除成员时主动失效缓存。缓存存在不构成继续访问的授权。'),
            section('共享只读', 'Aster 4.2 共享链接仅提供只读访问，链接有效期为 2 小时；链接不能用于调用上传或删除接口。'),
            section('成员上限', 'Aster 4.2 标准版每个知识库最多 120 份文档，企业版最多 300 份。两种版本需要按实际订阅识别。'),
            section('文档范围审计', 'Aster 4.2 搜索审计记录知识库标识、允许文档列表摘要和请求标识，不记录用户上传的完整正文。'),
            section('撤销凭证', 'Aster 4.2 撤销访问凭证后立即拒绝新请求，在途请求在写入前再次校验。不能用只读链接恢复写入权限。'),
            section('角色变更', 'Aster 4.2 角色变更需要 owner 授权并保留审计记录；不得只凭知识库名称推断操作者拥有管理权限。'),
            section('关联维护', 'Aster 4.2 多个知识库复用同一份向量记录，新增成员关联不重复生成文档向量。'),
        ]),
        'aster-operating-notes-v4.md': ('Aster 4.2 运维补充说明', [
            section('重试提醒', 'Aster 4.2 遇到向量入库错误，原文件和既有分块仍需保留。核对模型服务配置，利用原分块标识再次入库，不另建文档。', 'aster-index-retry'),
            section('版本提醒', 'Aster 4.2 只有 chunk_version 与 indexed_version 一致，向量索引才可用；重新切块后必须重新完成向量写入。', 'aster-index-version'),
            section('身份提醒', 'Aster 4.2 向量记录由 document_id 加 chunk_id 标识，重复执行写入应更新同一记录，避免持续增加重复向量。', 'aster-vector-identity'),
            section('范围提醒', 'Aster 4.2 搜索范围来自本知识库成员，用户指定文档也不能跳过成员归属校验。', 'aster-scope'),
            section('恢复校验', 'Aster 4.2 恢复文件后先核对 SHA-256，再验证分块集合和向量集合一致，最后开放语义检索。恢复期间仍不允许旧向量参与查询。'),
            section('告警分类', 'Aster 4.2 入库失败连续 4 次触发人工告警；该规则不表示系统自动重试 4 次，也不构成恢复耗时保证。'),
            section('日志定位', 'Aster 4.2 使用 X-Trace-ID 关联上传、切块和索引日志，原文档名称仅作展示，不作为唯一记录身份。'),
            section('回收检查', 'Aster 4.2 每周检查一次待清理记录，清理前再次核对成员关系，不删除其他知识库仍在使用的原文件。'),
            section('Linux 热加载', 'Aster 4.2 Linux 生产模式允许热加载 log.level；向量模型名、向量维度和存储目录修改均需重启并检查索引。'),
            section('监控留存', 'Aster 4.2 处理日志保留 45 天，安全审计记录保留 365 天。两者分别存储，不能按处理日志期限删除安全审计。'),
            section('清理许可', 'Aster 4.2 任何自动清理任务都不能跳过引用关系检查；维护账号的批量删除权限只作用于已撤销检索资格的记录。'),
            section('服务边界', 'Aster 4.2 文档没有公开客服响应分钟数、付费价格或 RTO 承诺。告警次数与日志留存不能换算成服务承诺。'),
        ]),
        'aster-windows-v4.md': ('Aster 4.2 Windows 开发环境', [
            section('启动命令', 'Aster 4.2 Windows 开发启动命令为 aster.exe serve --config .\\aster.yaml，数据目录为 .\\workspace。该模式只供本地开发。'),
            section('热加载例外', 'Aster 4.2 Windows 开发模式不支持热加载，修改 log.level 也必须重启；Linux 生产环境的日志热加载规则不能套用到 Windows。'),
            section('目录规则', 'Aster 4.2 Windows 开发索引目录必须位于本地磁盘，不使用网络共享盘。修改目录前先停止进程并保留原目录副本。'),
            section('开发上传', 'Aster 4.2 Windows 开发环境单个文件上限为 12 MB；该限制独立于标准版或企业版的知识库文档数量上限。'),
            section('开发连接', 'Aster 4.2 Windows 开发模式默认连接本机 5433 端口的 PostgreSQL，不能据此修改生产数据库地址。'),
            section('文件锁处理', 'Aster 4.2 Windows 删除索引目录遇到文件占用，应先关闭应用和相关调试进程，再检查占用句柄；不要强行清空仍在写入的目录。'),
            section('路径编码', 'Aster 4.2 Windows 配置文件使用 UTF-8 编码，含空格的命令行路径需要加引号。应用显示名不用于拼接存储路径。'),
            section('测试账号', 'Aster 4.2 Windows 开发账号不能访问生产知识库；复制测试数据前移除真实密钥和用户正文。'),
            section('开发监控', 'Aster 4.2 Windows 开发指标仅用于定位本机错误，不作为生产吞吐能力或可用率验证。'),
            section('本地恢复', 'Aster 4.2 Windows 开发恢复先还原原文件，再重新分块入库，不混用其他向量模型产生的历史索引。'),
        ]),
        'aster-archive-v2.md': ('Aster 2.0 历史版本', [
            section('旧维度', 'Aster 2.0 使用 384 维向量；4.2 使用 768 维，两个版本的向量库不能直接混用。'),
            section('旧重试', 'Aster 2.0 入库失败后需要人工重建文档记录；这是历史行为，4.2 应复用现有分块标识重试。'),
            section('旧成员限制', 'Aster 2.0 每个知识库最多 60 份文档，不区分标准版与企业版；该限制不适用于 4.2。'),
            section('旧分块限制', 'Aster 2.0 分块上限 400 字符、重叠上限 40 字符，迁移时应重新生成新版分块。'),
            section('旧上传限制', 'Aster 2.0 单个文件上限为 8 MB，不支持文字型 PDF，仅接受 UTF-8 Markdown。'),
            section('旧日志策略', 'Aster 2.0 普通日志保留 14 天，没有独立安全审计归档；旧策略不能套用到新版审计。'),
            section('旧连接策略', 'Aster 2.0 数据库连接池固定 8 个连接，连接池设置修改后必须重新启动。'),
            section('旧备份策略', 'Aster 2.0 每周保存一次原文件快照，保留 4 周；该备份不能直接恢复新版的成员授权状态。'),
            section('迁移校验', 'Aster 2.0 升级到 4.2 时先备份原文件与成员清单，再重新切块和生成 768 维向量，最后核对成员范围。'),
            section('历史平台', 'Aster 2.0 不支持 Windows 开发模式，历史启动脚本不能用于 Aster 4.2 Windows 开发。'),
        ]),
    }
    docs.update({name: {'title': title, 'sections': sections} for name, (title, sections) in extra.items()})
    return docs


def render_documents():
    texts, annotations = {}, []
    for name, doc in documents().items():
        text = '# ' + doc['title'] + '\n\n'
        for index, item in enumerate(doc['sections'], 1):
            header = '## ' + item['title'] + '\n\n'
            start = len(text)
            text += header + item['text'] + '\n\n'
            annotations.append({'id': f'{name}:{index}', 'document': name,
                                'start_char': start, 'end_char': len(text),
                                'body_start': start + len(header), 'text': item['text'],
                                'redundancy_group': item['redundancy_group'] or f'{name}:{index}'})
        texts[name] = text
    return texts, annotations


ALIASES = {'d': 'atlas-deploy-v2.md', 'c': 'atlas-config-v2.md', 'b': 'atlas-backup-v2.md',
           'a': 'atlas-auth-v2.md', 'm': 'atlas-monitor-v2.md', 'i': 'atlas-incident-v2.md',
           'cap': 'atlas-capacity-v2.md', 'mig': 'atlas-migrate-v2.md', 'rel': 'atlas-release-v2.md',
           'old': 'atlas-deploy-v1.md', 'neb': 'nebula-deploy-v2.md',
           'oc': 'orion-cache-v3.md', 'ow': 'orion-worker-v3.md', 'o1': 'orion-archive-v1.md',
           'ai': 'aster-index-v4.md', 'ap': 'aster-permissions-v4.md',
           'an': 'aster-operating-notes-v4.md', 'aw': 'aster-windows-v4.md', 'a2': 'aster-archive-v2.md'}


def evidence(alias, index, quote=None):
    name = ALIASES[alias]
    text = documents()[name]['sections'][index - 1]['text']
    quote = quote or text
    assert quote in text, (name, index, quote)
    return {'document': name, 'section': index, 'quote': quote}


def group(*refs):
    return {'alternatives': list(refs)}


def questions():
    rows = []
    def add(category, question, points, groups, *, dev=False, family=None, behavior='answer'):
        number = len(rows) + 1
        rows.append({'id': f'r{number:03}', 'category': category, 'question': question,
                     'split': 'dev' if dev else 'test', 'fact_family': family or f'fact-{number}',
                     'expected_behavior': behavior, 'expected_points': points, 'evidence_groups': groups})
    def g(alias, index, quote=None):
        return group(evidence(alias, index, quote))
    # 开发题仅使用 Orion 事实；验收题使用 Atlas/Nebula/Aster，避免同义题跨集合泄漏。
    direct = [
        ('Orion 3.0 缓存端口和连接超时分别是多少？', ['6388', '4秒'], [g('oc',1)], True, 'orion-connect'),
        ('Orion 3.0 普通任务重试次数和间隔是多少？', ['3次', '12秒'], [g('ow',2)], True, 'orion-retry'),
        ('Orion 3.0 工作线程默认值与上限是什么？', ['4', '12'], [g('ow',1)], True, 'orion-threads'),
        ('Orion 3.0 普通缓存和空结果分别保留多久？', ['20分钟', '30秒'], [g('oc',4)], True, 'orion-ttl'),
        ('Atlas 2.4 默认监听端口和就绪路径是什么？', ['8200', '/ready'], [g('d',2)], False, 'atlas-ready'),
        ('Atlas 2.4 请求超时与默认重试次数是多少？', ['20秒', '2次', '单次下游请求'], [g('c',1)], False, 'atlas-timeout'),
        ('Atlas 2.4 应用日志与审计日志各保留多久？', ['30天', '180天'], [g('m',4)], False, 'atlas-logs'),
        ('Atlas 2.4 editor 可以上传和删除吗？', ['可以上传', '不能删除', 'admin才能删除'], [g('a',2)], False, 'atlas-role'),
        ('Aster 4.2 的向量维度是多少，查询和文档模型需要一致吗？', ['768维', '同一模型版本'], [g('ai',1)], False, 'aster-dim'),
        ('Aster 4.2 标准版和企业版每个知识库分别最多多少文档？', ['120', '300'], [g('ap',8)], False, 'aster-edition'),
    ]
    for q,p,gs,dev,family in direct:
        add('direct',q,p,gs,dev=dev,family=family)
    paraphrases = [
        'Orion 新版缓存客户端连哪个口，建连最多等几秒？',
        'Orion 3.0 作业失败后最多再尝试几回，每回隔多久？',
        'Orion 新版任务进程一开始开几个执行线程，最多能开多少？',
        'Orion 3.0 查到的正常结果与查无结果的缓存寿命一样吗，分别多久？',
        'Atlas 2.4 接收连接的默认端口和允许切流的探针地址是什么？',
        'Atlas 新版一次下游调用等多久，会重试几回，这就是整轮时限吗？',
        'Atlas 2.4 普通运行记录和操作审计记录分别过多久清理？',
        'Atlas 新版编辑角色能放入资料吗，有资格移除资料吗？',
        'Aster 新版嵌入数组有几个分量，搜索与文档编码能换不同模型吗？',
        'Aster 4.2 两种商业版本的单库资料数量天花板各是多少？',
    ]
    for q, (_,p,gs,dev,family) in zip(paraphrases,direct):
        add('paraphrase',q,p,gs,dev=dev,family=family)
    cross = [
        ('Orion 3.0 缓存回源的并行上限、排队上限和满队列错误码是什么？',['8','200','C429'],[g('oc',7),g('oc',8)],True),
        ('Orion 3.0 缓存重建锁持续多久，任务幂等记录又保留多久？',['45秒','48小时'],[g('oc',3),g('ow',3)],True),
        ('Orion 3.0 停进程的等待上限、工作日志和失败任务正文保留多久？',['90秒','21天','72小时'],[g('ow',5),g('ow',7)],True),
        ('Atlas 2.4 上线需要什么依赖顺序、认证方式和完整备份内容？',['PostgreSQL15→Redis7.2→Atlas','生产OIDC','数据库和附件'],[g('d',3),g('a',1),g('b',1)],False),
        ('Atlas 2.4 E201 要查什么，数据库版本要求及修复后的切流条件是什么？',['database.url和网络','PostgreSQL15','/ready'],[g('i',1),g('d',3),g('d',2)],False),
        ('Atlas 2.4 备份如何暂停和恢复写入，恢复时的快照与校验要求是什么？',['暂停写入','SHA-256后恢复写入','同一快照数据库和附件'],[g('b',2),g('b',3)],False),
        ('Atlas 1.8 迁到 2.4 后怎么验收，回滚怎样处理数据库、附件及新版写入？',['数量与SHA-256','/ready','恢复同一快照数据库和附件','导出新版新增数据'],[g('mig',2),g('mig',4)],False),
        ('Atlas 2.4 的延迟告警、错误率告警及解除条件分别是什么？',['P95>800毫秒5分钟','错误率>2%3分钟','正常10分钟'],[g('m',1),g('m',6)],False),
        ('Aster 4.2 入库失败后如何重试，以及恢复检索前需要哪些校验？',['保留文件分块并复用标识','SHA-256','分块向量集合一致'],[group(evidence('ai',2),evidence('ai',4),evidence('an',1)),g('an',5)],False),
        ('Aster 4.2 Windows 的启动命令、数据目录，以及修改日志后是否重启？',['aster.exe serve --config .\\aster.yaml','.\\workspace','需要重启'],[g('aw',1),g('aw',2)],False),
    ]
    for q,p,gs,dev in cross:
        add('cross_document',q,p,gs,dev=dev)
    og = group(evidence('oc',2),evidence('oc',6),evidence('ow',6))
    retry = group(evidence('ai',2),evidence('ai',4),evidence('an',1))
    version = group(evidence('ai',3),evidence('ai',8),evidence('an',2))
    identity = group(evidence('ai',7),evidence('an',3))
    scope = group(evidence('ap',1),evidence('ap',4),evidence('an',4))
    duplicate = [
        ('Orion 3.0 过期缓存怎么重建，锁的粒度和有效期是什么？',['先标过期回主库','租户锁','45秒','不能清空全库'],[og,g('oc',3)],True),
        ('Orion 3.0 过期缓存重建步骤与满回源队列后的处理是什么？',['持租户锁回主库','C429','最多200任务'],[og,g('oc',8)],True),
        ('Orion 3.0 缓存重建怎样避免重复作业，幂等标识和保留时间是什么？',['租户锁','job_id','48小时'],[og,g('ow',3)],True),
        ('Aster 4.2 入库失败会删原文件吗，模型鉴权失败应查什么？',['保留文件和分块','复用标识重试','密钥与地域'],[retry,g('ai',5)],False),
        ('Aster 4.2 重新切块后索引什么时候才可用，向量维度和模型要求是什么？',['版本一致','768维','同一模型版本'],[version,g('ai',1)],False),
        ('Aster 4.2 检索如何限制知识库范围，contributor 可以删除吗？',['成员范围和归属校验','不能删除','owner可删除'],[scope,g('ap',2)],False),
        ('Aster 4.2 重复入库用什么身份更新，I422 怎么处理？',['document_id和chunk_id','核对模型及768维','不能截断旧向量'],[identity,g('ai',6)],False),
        ('Aster 4.2 文档移出一个知识库后还能被它检索吗，其他知识库会怎样？',['本库不能检索','保留原文件向量','其他关联库仍可使用'],[scope,g('ap',5)],False),
        ('Aster 4.2 入库失败后重试要另建记录吗，重新分块后旧向量能否继续查询？',['不另建记录','版本一致才可用'],[retry,version],False),
        ('Aster 4.2 比较知识库范围限制与向量更新身份，两者分别校验什么？',['校验成员归属','document_id和chunk_id'],[scope,identity],False),
    ]
    for q,p,gs,dev in duplicate:
        add('duplicate_noise',q,p,gs,dev=dev)
    versions = [
        ('Orion 1.1 和 3.0 缓存端口与连接超时分别是什么？',['6377/9秒','6388/4秒'],[g('o1',1),g('oc',1)],True),
        ('Orion 3.0 Linux 与 Windows 修改 worker.log_level 都能热加载吗？',['Linux可以','Windows必须重启'],[g('ow',8),g('ow',9)],True),
        ('Atlas 2.4 Windows 和 Linux 修改 log.level 是否都不需要重启？',['Linux需启用reload才可热加载','Windows必须重启'],[g('c',3),g('c',4)],False),
        ('Atlas 1.8 和 2.4 备份频率、保留时间与备份内容有什么区别？',['24小时/7天/仅数据库','6小时/14天/数据库附件'],[g('old',4),g('b',1)],False),
        ('Aster 2.0 与 4.2 的向量维度和知识库文档上限分别是什么？',['384/60','768/标准120企业300'],[g('a2',1),g('a2',3),g('ai',1),g('ap',8)],False),
        ('Aster 4.2 Linux 和 Windows 修改 log.level 是否都能热加载？',['Linux可以','Windows必须重启'],[g('an',9),g('aw',2)],False),
        ('Atlas 2.4 和 Nebula 2.4 的默认端口与就绪路径一样吗？',['Atlas8200/ready','Nebula9200/status'],[g('d',2),g('neb',1)],False),
        ('Atlas 2.4 磁盘已达到85%且队列积压，可以直接S17吗，满足条件时要怎么收尾？',['禁止直接S17先清理或扩容','恢复3线程','ready与读写测试再恢复写入'],[g('i',3),g('i',5)],False),
    ]
    for q,p,gs,dev in versions:
        add('version_exception',q,p,gs,dev=dev)
    negatives = [
        ('Orion 3.0 的缓存重建保证多少秒完成？',True),('Orion 3.0 商业订阅每月多少钱？',True),
        ('Atlas 2.4 承诺恢复数据最多需要几分钟？',False),('Atlas 2.4 8核16GB保证多少QPS？',False),
        ('Aster 4.2 客服保证几分钟响应？',False),('Atlas 2.4 故障退款比例是多少？',False)]
    for q,dev in negatives:
        add('unanswerable',q,['资料没有提供所问承诺或数值，不猜测'],[],dev=dev,behavior='refuse')
    ambiguous = [
        ('Orion 缓存应该配置哪个端口？', ['先确认Orion版本'],True),
        ('Orion 3.0 改日志级别需要重启吗？', ['先确认Linux生产还是Windows开发'],True),
        ('Atlas 默认监听端口是多少？', ['先确认Atlas版本'],False),
        ('Atlas 2.4 改日志级别要重启吗？', ['先确认操作系统和运行环境'],False),
        ('Aster 4.2 一个知识库最多可以加多少份文档？', ['先确认标准版还是企业版'],False),
        ('2.4 的就绪检查路径是什么？', ['先确认Atlas还是Nebula'],False)]
    for q,p,dev in ambiguous:
        add('clarification',q,p,[],dev=dev,behavior='clarify')
    # 预先固定 20 道回答验证题，覆盖每种题型；不根据检索结果挑题。
    answer_ids = {5,9,15,19,24,25,27,29,34,35,38,40,43,45,46,48,53,54,57,59}
    for row in rows:
        row['answer_check'] = int(row['id'][1:]) in answer_ids
    return rows


def write_dataset(directory=DATASET):
    directory = Path(directory)
    if (directory / 'manifest.json').exists():
        raise FileExistsError('专项集已经固定，不允许覆盖；新版本应使用新目录')
    texts, annotations = render_documents()
    (directory / 'documents').mkdir(parents=True, exist_ok=True)
    for name, text in texts.items():
        (directory / 'documents' / name).write_text(text, encoding='utf-8')
    for filename, rows in [('questions.jsonl',questions()),('sections.jsonl',annotations)]:
        (directory / filename).write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
    manifest = {'version':'retrieval-v1','documents':len(texts),'questions':60,
                'split':dict(Counter(q['split'] for q in questions())),
                'categories':dict(Counter(q['category'] for q in questions())),
                'top_k':5,'candidates':30,'mmr_lambda':0.8,'answer_check_questions':20,
                'source':'人工定义合成运维资料；Orion开发题与Atlas/Nebula/Aster验收题分离；非真实产品承诺',
                'hashes':{str(p.relative_to(directory)):hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sorted(directory.rglob('*')) if p.is_file()}}
    (directory/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    return manifest


if __name__ == '__main__':
    print(json.dumps(write_dataset(),ensure_ascii=False,indent=2))
