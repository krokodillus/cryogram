# Build a workflow

Describe what you want it to do, where the information comes from and what you want at the end. Cryogram explores what is needed, asks for missing details and proposes a plan. Once agreed, it builds and tests every step, asking for permission where needed.

Read **Here's the plan**, then choose **Looks right - go ahead** or ask for changes. If steps need to be added, removed or change type, Cryogram shows what will change and asks again. Renaming a step does not need approval.

While building, Cryogram asks before opening a browser window, sending something for real, connecting to a new web address or reading a folder you have not given it. Approving an address or folder allows this workflow to use it during the build and later runs.

For a browser step that only reads, **Yes, and don't ask again for <site>** allows later windows on that site until the build finishes. A step that sends through the browser asks for every window. **Yes, and don't ask again for this step** skips further send questions for that step until the build finishes.

Choosing **No** to a send or browser window still lets the step be built, but it is not tried for real again during the build. The first run is then its first real attempt.

**Here's what I built** tells you when it is ready. To stop building, use the **Stop** button beside **Send**, or type a message to stop the current work and tell Cryogram what to do next.
