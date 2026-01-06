
# How to contribute to Acexy

Acexy is software shipped with no guarantees. This means:

- The software is provided as is.
- The software is endlessly tested. This **does not mean** it will always work.
- The software is shipped under the GNU GPLv3 license - you should follow it.
- The software is free software, maintained by public, selfless contributors. The contributors
  may or may not answer to your petitions.

If you are here, this means you have an interest in the project! Thanks a lot 🥳. Contributing to
Acexy should be quite straightforward, so make sure to always follow these guidelines.

## Did you find a bug?

That's awesome 🦋!! First, before going straight to the [Issues](https://github.com/Javinator9889/acexy/issues) page, first check:

1. There is **no issue** covering the bug you just have found on the [Issues](https://github.com/Javinator9889/acexy/issues) page.
2. Fill the appropriate Bug Template when creating the issue. Be sure to include a **title and
clear description**, as much relevant information as possible, and a **code sample or executable
test case** demonstrating the expected behavior that is not occurring.

Acexy tries to not output too much messages at a time. However, it has the debug mode
available to capture as much information as possible. When filling a bug, remember always to:

- Add the exact steps you took for us to reproduce the issue.
- Run the program setting `ACEXY_LOG_LEVEL=DEBUG` and grab **all the output**.
- Remember adding all the relevant information about your system, such as:
  - The Acexy version.
  - Whether you are running it inside Docker or not.
    - If so, the Docker version you are using, and the Acexy's Docker Image tag.
  - Your host OS - either it is Linux, Windows, or macOS.
  - The exact configuration you are using to run Acexy and AceStream - the `EXTRA_FLAGS` variable,
    the Acexy's environment variables, etc.

That all information helps us a lot when fixing problems. Do not hesitate contacting us if
you need any help.

### Did you write a patch that fixes it?

WOW!! THAT'S A STEP FURTHER!! Go ahead and create a GitHub Pull Request. Don't you know where
to start? Be sure to check [how to create a pull request](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/proposing-changes-to-your-work-with-pull-requests/creating-a-pull-request).

The process is:

- Open a GitHub Pull Request with the change.
- Ensure the PR description clearly describes the problem you are addressing. If you have
  created an issue, make sure to add the appropriate issue number.
- Review the existing code to follow the guidelines and style.
- Wait for the revision of one of the maintainers. Be open-minded with comments, concerns, and
  modifications that may show up.

## Adding a new feature

We encourage every developer to start thinking about the potential future of the project. New
ideas are welcomed, as they will drive the project to the next step.

Considering the magnitude of an addition or a change, the process should be precisely
documented and done. This allows future developers to determine the path for an action,
reduces the technical debt, and allows anyone to follow the steps taken.

The process consists on:

1. Open an issue, describing the functionality you'd like to have in Acexy.
2. You can start **coding** if you want to do the changes yourself. However...
3. Wait for the maintainers' evaluation, comments, and enthusiasm. This is crucial
   so you don't work on something that may be rejected in the future.
4. Once all the comments are gathered and the issue is considered mature enough, the
   development will start shortly.

If you have started the development on your own, follow the same guidelines as the ones
described in [Did you write a patch that fixes it?](#did-you-write-a-patch-that-fixes-it).

* * *

Thank you so much for your support! This is a coordinated volunteer effort. Now, you
belong to the contributors' side 😎.
