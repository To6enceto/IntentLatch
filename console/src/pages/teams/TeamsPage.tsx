import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { hasRole, useUser } from "../../auth";
import { Icon } from "../../components/Icon";
import { Button } from "../../components/ui/Button";
import { useResource } from "../../lib/useResource";
import { CreateTeamDialog } from "./dialogs";
import { TeamDetail } from "./TeamDetail";
import { teamsApi, type Team } from "./teamsApi";

export function TeamsPage() {
  const canEdit = hasRole(useUser(), "admin");
  const teams = useResource(teamsApi.list, []);
  const [searchParams, setSearchParams] = useSearchParams();
  const [creating, setCreating] = useState(false);
  const selected = teams.data?.find((team) => team.id === searchParams.get("team")) ?? teams.data?.[0];

  function replaceTeam(next: Team) {
    teams.update((list) => list.map((team) => (team.id === next.id ? next : team)));
  }

  return (
    <>
      <div className="page-heading page-heading-row">
        <div>
          <h1>Teams & identities</h1>
          <p>Teams, the models they may call, and the employee tokens that identify every request.</p>
        </div>
        {canEdit && <Button onClick={() => setCreating(true)}><Icon name="plus" />New team</Button>}
      </div>

      {!canEdit && (
        <p className="notice"><Icon name="eye" />You have read-only access. An administrator manages teams and employee tokens.</p>
      )}
      {teams.error && (
        <div className="notice is-error" role="alert">
          <Icon name="alert" /><span>{teams.error}</span>
          <Button variant="outline" size="sm" className="ml-auto" onClick={teams.reload}>Retry</Button>
        </div>
      )}

      {teams.loading && !teams.data && <p className="panel empty-state" role="status">Loading teams…</p>}
      {teams.data?.length === 0 && (
        <section className="panel empty-state" aria-labelledby="no-teams-title">
          <h3 id="no-teams-title">No teams yet</h3>
          <p>{canEdit ? "Create a team, choose its models, then add employees to issue their tokens." : "An administrator can create the first team."}</p>
          {canEdit && <div className="btn-row"><Button onClick={() => setCreating(true)}><Icon name="plus" />New team</Button></div>}
        </section>
      )}
      {teams.data && selected && (
        <div className="teams-layout">
          <nav className="panel teams-list" aria-labelledby="teams-list-title">
            <header className="panel-header">
              <h2 id="teams-list-title">Teams<span className="count">{teams.data.length}</span></h2>
            </header>
            <ul>
              {teams.data.map((team) => (
                <li key={team.id}>
                  <Link to={{ search: `?team=${encodeURIComponent(team.id)}` }} className="team-item" aria-current={team.id === selected.id ? "page" : undefined}>
                    <span className="team-name">{team.name}</span>
                    <span className="team-models">
                      {team.authorized_models.map((model) => <span key={model} className="badge is-model">{model}</span>)}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
          <TeamDetail key={selected.id} team={selected} canEdit={canEdit} onTeamChange={replaceTeam} />
        </div>
      )}

      {creating && (
        <CreateTeamDialog
          onClose={() => setCreating(false)}
          onCreated={(team) => {
            teams.update((list) => [...list, team]);
            setCreating(false);
            setSearchParams({ team: team.id });
          }}
        />
      )}
    </>
  );
}
