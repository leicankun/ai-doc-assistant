"""Author the 30-question fixed dataset; preserve the first five pilot questions."""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
source = HERE.parent / 'uploads/11111.md'
(HERE / 'documents/odysseus.md').write_text(source.read_text(encoding='utf-8'), encoding='utf-8')
questions = [json.loads(line) for line in (HERE/'questions.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()][:5]

# Each row is manually authored: source, category, question, expected points, exact evidence.
rows = [
('odysseus','direct','奥德修斯是哪座岛的国王？',['伊萨卡'],['奥德修斯是伊萨卡国王、特洛伊战争的关键智将，也是《奥德赛》的主人公']),
('odysseus','direct','奥德修斯设计了什么计策攻陷特洛伊？',['木马计','空心木马让希腊军潜入城中'],['木马计的提出者：他设计空心木马，使希腊军潜入城中，一举攻陷特洛伊。']),
('odysseus','direct','谁识破了奥德修斯装疯避战？',['帕拉墨得斯'],['装疯避战：因预言自己将受二十年磨难，他曾假装疯狂，但被帕拉墨得斯识破。']),
('odysseus','direct','在喀耳刻岛，谁帮助奥德修斯战胜女巫？他停留多久？',['赫耳墨斯','一年'],['喀耳刻岛：同伴被变成猪，他在赫耳墨斯帮助下战胜女巫，并在岛上停留一年。']),
('odysseus','direct','奥德修斯在冥界向谁求取什么信息？',['忒瑞西阿斯','归乡预言'],['冥界之旅：向先知忒瑞西阿斯求取归乡预言。']),
('odysseus','direct','塞壬歌声的诱惑是怎样避过的？',['蜡封同伴耳朵','自己绑在桅杆上'],['塞壬歌声：以蜡封同伴耳朵、自己绑桅杆的方式避过诱惑。']),
('odysseus','direct','通过斯库拉与卡律布狄斯时牺牲了多少同伴？',['六名','换取船只存活'],['斯库拉与卡律布狄斯：牺牲六名同伴换取船只存活。']),
('odysseus','direct','卡吕普索囚留奥德修斯多久？他为什么拒绝不朽？',['七年','坚持回家'],['卡吕普索囚留七年：拒绝女神的不朽诱惑，坚持回家。']),
('pilot','cross_section','上传成功后原文件和文档信息各保存在哪里，重新分块后还要做什么？',['原文件保存 uploads','文档信息保存 SQLite','再次向量入库更新 Chroma'],['上传成功后，原文件保存到 uploads 目录，文档信息保存到 SQLite。','重新生成分块后，需要再次执行向量入库，更新 Chroma 中的记录。']),
('pilot','cross_section','分块如何选择切分边界，问题向量又怎样保证和分块向量一致？',['优先标题段落句子','超长按字符兜底','同用 text-embedding-v4 和 1024 维'],['优先在标题、段落和句子边界切分，超长内容按字符兜底。','分块向量使用 text-embedding-v4 模型，维度为 1024。问题向量使用相同的模型和维度。']),
('pilot','cross_section','BM25 和 dense 各自按什么数值排序，hybrid 如何合并它们？',['BM25 分数越大越靠前','dense 余弦距离越小越靠前','RRF 融合排名而非直接加原始分数'],['BM25 根据词语匹配计算分数，分数越大排名越靠前。','dense 使用余弦距离检索分块向量，距离越小排名越靠前。','hybrid 使用 RRF 融合两路排名，平滑常数为 60']),
('pilot','cross_section','多轮追问怎么检索，流式回答在什么时候才会保存为成功历史？',['最近 5 轮','必要时改写重新检索','正常完成通过引用编号校验才保存并发 done','停止失败不保存成功轮次'],['多轮 RAG 使用最近 5 轮历史理解追问，必要时改写问题后重新检索。','只有正常完成并通过引用编号校验后才保存本轮历史，再发送 done。停止或生成失败的回答不作为成功轮次保存。']),
('odysseus','cross_section','奥德修斯在独眼巨人洞穴中如何脱身，此事又为什么阻碍归乡？',['刺瞎波吕斐摩斯','自称无人','遭波塞冬追击'],['独眼巨人洞穴：刺瞎波吕斐摩斯并自称“无人”脱身，却因此遭波塞冬追击。']),
('odysseus','cross_section','风袋和太阳神牛群这两次事件中，同伴分别做了什么，造成什么后果？',['误开风袋船队被吹回原点','违禁宰牛宙斯击沉船只','仅奥德修斯生还'],['风袋误启：风神赠风袋助返乡，但船员误开，船队被吹回原点。','太阳神牛群：同伴违禁宰牛，宙斯击沉船只，仅他一人生还。']),
('odysseus','cross_section','返乡时谁帮奥德修斯伪装，他又与谁相认并密谋复仇？',['雅典娜帮助化身乞丐','儿子忒勒玛科斯'],['伪装乞丐回宫：在雅典娜帮助下，他化身乞丐潜入宫殿。','父子相认：与忒勒玛科斯密谋复仇。']),
('pilot','paraphrase','一大段文字超过长度限制时会怎么拆，前后两块最多留多少重复内容？',['500 字符上限','自然边界优先超长按字符','最多重复 100 字符'],['分块以 500 个字符为长度上限，最多重叠 100 个字符。优先在标题、段落和句子边界切分，超长内容按字符兜底。']),
('pilot','paraphrase','回答旁边的方括号数字能沿用到下一次回答吗？原文来源是谁提供的？',['编号只对本轮有效','后端提供原文和来源'],['引用编号只对本轮回答有效。后端检查编号是否存在，引用原文和来源由后端提供。']),
('pilot','paraphrase','追问时系统会把我的原话覆盖掉吗，实际用来查资料的问法存在哪里？',['原问题和改写问题分别保存','不会用改写覆盖原问题'],['用户原问题和用于检索的改写问题分别保存。']),
('odysseus','paraphrase','那个用大木头马拿下城池的英雄，战后在海上辗转了几年？',['奥德修斯','十年'],['奥德修斯是伊萨卡国王、特洛伊战争的关键智将，也是《奥德赛》的主人公，他以木马计攻陷特洛伊，并在战后经历十年漂泊、最终返乡复仇与团聚的传奇旅程。']),
('odysseus','paraphrase','水手吃了让他们不想回家的东西后，领队怎么处理？',['食莲人之地','奥德修斯强行带走船员'],['食莲人之地：船员因食莲忘返，他强行带走他们。']),
('odysseus','paraphrase','女巫把伙伴变成了哪种动物？',['喀耳刻','猪'],['喀耳刻岛：同伴被变成猪，他在赫耳墨斯帮助下战胜女巫，并在岛上停留一年。']),
('pilot','unanswerable','服务每月订阅价格是多少元？',[],[]),
('pilot','unanswerable','系统多久自动备份一次 SQLite，备份保留多少天？',[],[]),
('odysseus','unanswerable','奥德修斯出生的公元前年份是多少？',[],[]),
('odysseus','unanswerable','奥德修斯的木马长宽高各多少米？',[],[]),
]
missing = {
    27: '资料未提供订阅价格，不得编造金额。',
    28: '资料仅提到 SQLite 存储，没有自动备份周期和保留期限。',
    29: '资料未给出出生年份，不得用外部常识猜测。',
    30: '资料提到空心木马，但未给出长宽高。',
}
for number, (name, category, question, points, quotes) in enumerate(rows, start=6):
    entry = dict(id=f'q{number:03}', document=f'documents/{name}.md', type=category,
                 question=question, answerable=category!='unanswerable', expected_points=points,
                 evidence=[dict(source_name=f'{name}.md', page_number=None, quote=quote) for quote in quotes])
    if not entry['answerable']:
        entry['missing_information'] = missing[number]
    text = (HERE/entry['document']).read_text(encoding='utf-8')
    assert all(e['quote'] in text for e in entry['evidence']), entry['id']
    questions.append(entry)
assert len(questions) == 30
(HERE/'questions.jsonl').write_text(''.join(json.dumps(q, ensure_ascii=False)+'\n' for q in questions), encoding='utf-8')
print('Wrote 30 questions with verified verbatim evidence.')
