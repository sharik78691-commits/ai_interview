/** AI model catalog (mirrors GET /api/ai/models). No secrets — availability only. */

export interface AiModel {
  key: string;
  provider: string;
  displayName: string;
  modelId: string;
  available: boolean;
  enabled: boolean;
  freeTier: boolean;
  capabilities: string;
  isDefault: boolean;
  priority: number;
}

export interface AiModelsResponse {
  models: AiModel[];
  default: string;
}
