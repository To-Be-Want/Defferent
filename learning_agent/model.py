"""Replaceable OpenAI-compatible tool-call transport, separate from business logic."""
import requests
import material_analyst as ma
from study_preferences import get_preferences


def complete(messages, tools):
    if not get_preferences()['allow_cloud_model']:
        raise PermissionError('当前账号未允许在线模型')
    cfg = ma.sniff_llm_config()
    url = cfg['base_url'].rstrip('/')
    if not url.endswith('/chat/completions'):
        url += '/chat/completions'
    body = {'model': cfg['model'], 'messages': messages, 'tools': tools,
            'temperature': .2, 'max_tokens': 6000}
    if cfg['model'].lower().startswith('deepseek'):
        body['thinking'] = {'type': 'disabled'}
    response = requests.post(url, headers={'Authorization': 'Bearer '+cfg['api_key']}, json=body, timeout=(15, 90))
    response.raise_for_status()
    choice = response.json()['choices'][0]
    if choice.get('finish_reason') == 'length':
        raise ValueError('模型输出达到长度上限，请缩小范围重试')
    value = choice['message']
    if not value.get('tool_calls') and not str(value.get('content') or '').strip():
        raise ValueError('模型没有返回完整答案')
    # Reasoning is never shown or stored as a student answer.
    return {key: value[key] for key in ('role', 'content', 'tool_calls') if key in value}
