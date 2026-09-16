# Set variables and reuse them

Edit a workflow's saved variables on the **Inputs** tab. Create an environment under **Environments** to reuse folders, access keys and other variables across workflows. Tell the AI agent if a value should be chosen at the start of each run instead.

Edit shared variables in **Environments**. Attached workflows use changed values on their next run. If more than one environment supplies the same name, **In use** marks the selected value; choose **Use** beside another value to switch.

Secrets such as API keys are stored encrypted. Enter them in the separate box Cryogram provides instead of a chat message, so the AI agent sees their names but never their values. A saved secret cannot be read back, only replaced.

Deleting an environment asks for confirmation. Workflows that used its values have empty fields afterwards and pause to ask for them on their next run.
