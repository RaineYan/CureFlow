// Validate only user-supplied settings. Never infer key type from a prefix,
// persist a secret in browser storage, or accept a custom destination URL.
export function providerSetupPayload(provider, key, model, useExisting = false) {
  if (!['openai', 'qwen', 'deepseek', 'doubao'].includes(provider)) throw new Error('不支持的模型服务');
  const modelId = String(model || '').trim();
  if (!modelId || modelId.length > 128 || /[\s\x00-\x1f]/.test(modelId)) throw new Error('请从该服务控制台复制实际开通的模型 ID；不要填写密钥名称');
  if (useExisting) return { provider, modelId, useExistingEnvironmentKey: true };
  const apiKey = String(key || '').trim();
  if (apiKey.length < 8 || apiKey.length > 4096 || /[\s\x00-\x1f]/.test(apiKey)) throw new Error('API Key 格式不正确，请重新粘贴完整密钥');
  if (modelId === apiKey) throw new Error('模型 ID 与 API Key 不能相同，请将密钥只填入密码框');
  return { provider, modelId, apiKey };
}
