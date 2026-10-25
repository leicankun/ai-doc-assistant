"""流式累积工具协议，只把最终 JSON 的 answer 字段作为回答预览。"""
from jiter import from_json
from openai import ContentFilterFinishReasonError, LengthFinishReasonError


def stream_completion(client, kwargs, check_runtime, emit):
    preview = ''
    # SDK 负责累积跨 chunk 的工具名称、参数和调用 ID。
    with client.chat.completions.stream(**kwargs) as stream:
        try:
            for event in stream:
                check_runtime()
                if event.type != 'content.delta':
                    continue
                try:
                    partial = from_json(event.snapshot.encode('utf-8'), partial_mode='trailing-strings')
                except ValueError:
                    continue
                if not isinstance(partial, dict):
                    continue
                # 目录和依据不足的回答由后端生成，不展示模型的临时措辞。
                if partial.get('status') not in ('grounded', 'clarification', 'general'):
                    continue
                answer = partial.get('answer')
                if isinstance(answer, str) and answer.startswith(preview):
                    delta = answer[len(preview):]
                    if delta:
                        emit({'type': 'delta', 'content': delta})
                        preview = answer
            completion = stream.get_final_completion()
        except (LengthFinishReasonError, ContentFilterFinishReasonError) as exc:
            # 保持与非流式路径相同的截断校验及有限纠正行为。
            completion = exc.completion
    if completion.choices[0].message.tool_calls and preview:
        emit({'type': 'reset'})
        preview = ''
    return completion, preview
