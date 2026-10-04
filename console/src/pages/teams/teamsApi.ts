import { api } from "../../lib/api";

// Mirrors MODEL_IDS in gateway/src/intentlatch/llms.py.
export const MODEL_IDS = ["corporate-a", "corporate-b"] as const;
export const TEAM_NAME_MAX = 64;
export const EMPLOYEE_NAME_MAX = 128;

export type Team = { id: string; name: string; authorized_models: string[]; created_at: string };

export type Employee = {
  id: string;
  team_id: string;
  team: string;
  name: string;
  token_issued_at: string;
  revoked_at: string | null;
  created_at: string;
};

/** The gateway shows an employee token only in these responses. */
export type IssuedToken = { employee: Employee; token: string };

const path = encodeURIComponent;

export const teamsApi = {
  list: () => api<{ teams: Team[] }>("/admin/teams").then(({ teams }) => teams),
  create: (name: string, models: string[]) =>
    api<Team>("/admin/teams", { method: "POST", body: { name, authorized_models: models } }),
  setModels: (teamId: string, models: string[]) =>
    api<Team>(`/admin/teams/${path(teamId)}`, { method: "PATCH", body: { authorized_models: models } }),
  employees: (teamId: string) =>
    api<{ employees: Employee[] }>(`/admin/teams/${path(teamId)}/employees`).then(({ employees }) => employees),
  addEmployee: (teamId: string, name: string) =>
    api<IssuedToken>(`/admin/teams/${path(teamId)}/employees`, { method: "POST", body: { name } }),
  reissue: (employeeId: string) => api<IssuedToken>(`/admin/employees/${path(employeeId)}/token`, { method: "POST" }),
  revoke: (employeeId: string) => api<Employee>(`/admin/employees/${path(employeeId)}/revoke`, { method: "POST" }),
};

export function sameModels(a: readonly string[], b: readonly string[]) {
  return a.length === b.length && a.every((model) => b.includes(model));
}
