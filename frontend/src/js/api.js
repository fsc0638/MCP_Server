// API layer for frontend modularization.
export const API_BASE = "";

export async function getModels() {
  const resp = await fetch(`${API_BASE}/api/models`);
  return resp.json();
}

