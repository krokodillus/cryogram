// The workflow page's shared state: one object the workspace, the live-turn machinery and the shell read and write
export const S = {
  workflow: null,
  workflowEnvs: [],
  appSettings: null,
  viewToken: 0,
  workflowTab: "workflow",
  chatBusy: false,
  runBusy: false,
  currentTurnId: "",
  pendingFirstMessage: null,
  cellStatesRef: null,
  focusStepRef: null,
  buildStateRef: null,
  turnPollFor: null,
};

// Only the turn whose view token is current may paint; a superseded turn goes inert
export const owns = (s) => s.token === S.viewToken;
