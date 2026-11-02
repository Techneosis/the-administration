"""Provenance: which build a contract tested. A redteam once judged one
build while believing it was another (rooms installed whatever was at a
shared path, verifiers read a branch that had moved on, the branch was cut
without the local default branch's fixes). Run through tests/isolated.sh.
"""
import json
import os
import subprocess
import unittest

from test_administration import Base, binary, professional, write, setUpModule  # noqa: F401


class Provenance(Base):
    def setUp(self):
        super().setUp()
        write(os.path.join(self.project, "app.txt"), "one\n")
        self.git("add", ".")
        self.git("commit", "-qm", "base")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.project, check=True, env=self.env,
                              capture_output=True, text=True).stdout.strip()

    def head(self):
        return self.git("rev-parse", "HEAD")

    def contract(self, cid):
        return json.load(open(os.path.join(self.books, "contracts", cid, "contract.json")))

    def phone(self, start, extra_toml=""):
        """A phone whose setup prints what start says (BUILD_COMMIT=...)."""
        write(os.path.join(self.project, ".administration", "roster.toml"), f'''
            [equipment.phone]
            about = "A phone."
            setup = ".administration/equipment/phone/start"
            server = ".administration/equipment/phone/server"
            tools = ["poke"]
            {extra_toml}
            ''')
        write(os.path.join(self.project, ".administration", "equipment", "phone", "start"), "#!/bin/sh\n" + start + "\n", 0o755)
        write(os.path.join(self.project, ".administration", "equipment", "phone", "server"), "#!/bin/sh\nexit 0\n", 0o755)
        self.hire_file("thumb", extra="  browser: false\n  uses: [phone]")

    def test_the_contract_records_its_commit_and_status_and_report_print_it(self):
        self.hire_file("reader", kind="critic")
        head = self.head()
        cid = self.place("x", "--cast", "reader")
        self.assertEqual(self.contract(cid)["commit"], head)
        self.assertNotIn("work", self.contract(cid))
        self.assertIn(f"Tested build: {head[:10]}", self.cli("status", cid).stdout)
        self.cli("wait", cid)
        self.cli("close", cid)
        self.assertIn(f"Tested build: {head[:10]}.", self.cli("report", cid).stdout)

    def test_uncommitted_work_is_part_of_the_tested_build(self):
        self.hire_file("reader", kind="critic")
        write(os.path.join(self.project, "app.txt"), "one\nuncommitted\n")
        write(os.path.join(self.project, "new.txt"), "brand new\n")
        write(os.path.join(self.project, ".env"), "SECRET=hunter2\n")
        cid = self.place("x", "--cast", "reader")
        c = self.contract(cid)
        self.assertRegex(c["work"], r"^work-[0-9a-f]{10}$")
        self.assertIn("plus uncommitted work", self.cli("status", cid).stdout)
        # The copy critics read has the work, and never a secret.
        self.assertEqual(open(os.path.join(c["checkout"], "app.txt")).read(), "one\nuncommitted\n")
        self.assertEqual(open(os.path.join(c["checkout"], "new.txt")).read(), "brand new\n")
        self.assertFalse(os.path.exists(os.path.join(c["checkout"], ".env")))
        # The person's repository is untouched: nothing staged, nothing stashed.
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "")
        self.assertEqual(self.git("stash", "list"), "")
        self.cli("wait", cid)

    def test_critics_and_verifiers_read_the_project_as_it_was_placed(self):
        # The builder kept committing while the contract ran, and verifiers
        # reading the branch tip checked real findings false.
        self.hire_file("reader", kind="critic")
        cid = self.place("x", "--cast", "reader")
        self.cli("wait", cid)
        c = self.contract(cid)
        cwd = self.call(cid, "reader")["cwd"]
        self.assertEqual(os.path.realpath(cwd), os.path.realpath(c["checkout"]))
        self.assertNotEqual(os.path.realpath(cwd), os.path.realpath(self.project))
        self.assertFalse(os.path.exists(os.path.join(cwd, ".git")))  # files, nothing that points back
        self.assertIn("project at your working directory", open(os.path.join(self.pdir(cid, "reader"), "brief.md")).read())
        # Later work on the branch doesn't reach what's checked.
        write(os.path.join(self.project, "app.txt"), "one\nfixed later\n")
        self.git("commit", "-qam", "later")
        self.cli("close", cid)
        self.cli("verify", cid, env={"ADMINISTRATION_MATCH": "off"})
        seen = [json.loads(l) for l in open(os.path.join(self.books, "contracts", cid, "fake-verifier-seen.jsonl"))]
        self.assertEqual(os.path.realpath(seen[0]["project"]), os.path.realpath(c["checkout"]))
        self.assertEqual(seen[0]["tested_commit"], c["commit"])
        self.assertEqual(open(os.path.join(seen[0]["project"], "app.txt")).read(), "one\n")

    def test_a_pruned_copy_is_made_once_for_every_verifier(self):
        # Four verifiers at once each made the copy, took it from under each
        # other, and the losers read the project as it is now.
        self.hire_file("reader", kind="critic")
        cid = self.place("x", "--cast", "reader")
        self.cli("wait", cid)
        self.cli("close", cid)
        checkout = os.path.join(self.books, "contracts", cid, ".checkout")
        subprocess.run(["rm", "-rf", checkout], check=True)
        write(os.path.join(self.project, "app.txt"), "moved on\n")
        self.git("commit", "-qam", "later")
        p = self.cli("verify", cid, "--batch", "1", env={"ADMINISTRATION_MATCH": "off"})
        self.assertIn("2 verifiers", p.stdout)
        self.assertNotIn("as it is now", p.stderr)
        seen = [json.loads(l) for l in open(os.path.join(self.books, "contracts", cid, "fake-verifier-seen.jsonl"))]
        self.assertEqual(len(seen), 2)
        for v in seen:
            self.assertEqual(os.path.realpath(v["project"]), os.path.realpath(checkout))
        self.assertEqual(open(os.path.join(checkout, "app.txt")).read(), "one\n")
        self.assertEqual([n for n in os.listdir(os.path.dirname(checkout)) if n.startswith(".checkout-")], [])

    def test_a_contract_with_no_commit_says_verify_reads_the_project_as_it_is(self):
        self.hire_file("reader", kind="critic")
        cid = self.place("x", "--cast", "reader")
        self.cli("wait", cid)
        self.cli("close", cid)
        path = os.path.join(self.books, "contracts", cid, "contract.json")
        c = json.load(open(path))
        for k in ("commit", "checkout"):
            del c[k]  # as a contract from before they were kept
        json.dump(c, open(path, "w"))
        p = self.cli("verify", cid, env={"ADMINISTRATION_MATCH": "off"})
        self.assertIn("recorded no commit", p.stderr)
        self.assertIn("reads the project as it is now", p.stderr)
        self.assertNotIn("Tested build", self.cli("report", cid).stdout)

    def test_the_copy_is_made_at_place_only_when_something_needs_it(self):
        # Users only, on a clean tree: the commit is enough until verify.
        self.phone(f"echo BUILD_COMMIT={self.head()}")
        cid = self.place("x", "--cast", "thumb")
        self.cli("wait", cid)
        self.assertNotIn("checkout", self.contract(cid))
        self.cli("close", cid)
        self.cli("verify", cid, env={"ADMINISTRATION_MATCH": "off"})
        self.assertTrue(os.path.isdir(self.contract(cid)["checkout"]))
        # Uncommitted work can't be had later: copied at place.
        write(os.path.join(self.project, "app.txt"), "one\nwip\n")
        self.phone(f"echo BUILD_COMMIT={self.head()}")
        cid = self.place("x", "--cast", "thumb")
        self.assertEqual(open(os.path.join(self.contract(cid)["checkout"], "app.txt")).read(), "one\nwip\n")
        self.cli("wait", cid)

    def test_signing_keys_and_service_credentials_stay_out_of_the_copy(self):
        self.hire_file("reader", kind="critic")
        for name in ("AuthKey_ABC123.p8", "app/release.keystore", "app/upload.jks", "app/service-account.json",
                     "app/google-services.json", "ios/GoogleService-Info.plist", ".aws/credentials"):
            write(os.path.join(self.project, name), "secret\n")
        write(os.path.join(self.project, "app/kept.txt"), "fine\n")
        cid = self.place("x", "--cast", "reader")
        co = self.contract(cid)["checkout"]
        found = sorted(os.path.relpath(os.path.join(d, f), co) for d, _, fs in os.walk(co) for f in fs)
        self.assertEqual(found, ["app.txt", "app/kept.txt"])
        self.cli("wait", cid)
        deny = json.load(open(os.path.join(self.pdir(cid, "reader"), "settings.json")))["permissions"]["deny"]
        self.assertIn("Read(**/*.p8)", deny)  # the wall knows them too

    def test_equipment_reports_its_build_and_the_contract_keeps_it(self):
        head = self.head()
        self.phone(f"echo BUILD_COMMIT={head[:7]}\necho BUILD_APP=org.example.notes\necho BUILD_VERSION=1.4.0")
        cid = self.place("x", "--cast", "thumb")
        self.cli("wait", cid)
        s = self.status(cid)["status"]["thumb"]
        self.assertEqual(s["state"], "back", s)
        self.assertEqual(s["builds"], [{"from": "phone", "commit": head, "app": "org.example.notes", "version": "1.4.0"}])
        self.assertIn(f"build phone: {head[:10]} (org.example.notes 1.4.0)", self.cli("status", cid).stdout)
        self.cli("close", cid)
        self.assertIn(f"thumb's room ran phone: {head[:10]} (org.example.notes 1.4.0)", self.cli("report", cid).stdout)

    def test_a_build_from_another_commit_is_refused_unless_chosen(self):
        old = self.head()
        write(os.path.join(self.project, "app.txt"), "two\n")
        self.git("commit", "-qam", "newer")
        self.phone(f"echo BUILD_COMMIT={old}")
        cid = self.place("x", "--cast", "thumb", early=3)
        self.cli("wait", cid, check=3)
        reason = self.status(cid)["status"]["thumb"]["reason"]
        self.assertIn(f"phone runs a build of {old[:10]}", reason)
        self.assertIn(f"--build {old[:10]}", reason)
        self.cli("close", cid)
        # On purpose: the person says that build is the one to test.
        cid = self.place("x", "--cast", "thumb", "--build", old[:8])
        self.cli("wait", cid)
        self.assertEqual(self.status(cid)["status"]["thumb"]["state"], "back")
        self.assertEqual(self.contract(cid)["build"], old)
        self.assertIn(f"Tested build: {old[:10]}, chosen with --build", self.cli("status", cid).stdout)
        self.assertIn("not a commit here", self.cli("place", "x", "--cast", "thumb", "--build", "nope!", check=1).stderr)

    def test_a_short_build_matches_a_whole_one_either_way_round(self):
        # --build with the start of someone else's commit, and equipment
        # that reports all of it: the case --build is for.
        foreign = "0123456789abcdef0123456789abcdef01234567"
        self.phone(f"echo BUILD_COMMIT={foreign}")
        cid = self.place("x", "--cast", "thumb", "--build", "0123456")
        self.cli("wait", cid)
        s = self.status(cid)["status"]["thumb"]
        self.assertEqual(s["state"], "back", s)
        self.assertEqual(s["builds"][0]["commit"], foreign)
        self.cli("close", cid)
        # Refused, the suggestion is the whole commit, which works.
        cid = self.place("x", "--cast", "thumb", early=3)
        self.cli("wait", cid, check=3)
        self.assertIn(f"--build {foreign} to test", self.status(cid)["status"]["thumb"]["reason"])
        self.cli("close", cid)
        cid = self.place("x", "--cast", "thumb", "--build", foreign)
        self.cli("wait", cid)
        self.assertEqual(self.status(cid)["status"]["thumb"]["state"], "back")

    def test_a_dirty_build_is_said_even_on_a_clean_contract(self):
        self.phone(f"echo BUILD_COMMIT={self.head()[:9]}-dirty")
        cid = self.place("x", "--cast", "thumb")
        self.cli("wait", cid)
        s = self.status(cid)["status"]["thumb"]
        self.assertEqual(s["builds"][0]["note"], "built with uncommitted work, though the contract has none", s)

    def test_a_build_that_isnt_a_commit_is_refused_asking_for_one(self):
        self.phone("echo BUILD_COMMIT=v1.0")
        cid = self.place("x", "--cast", "thumb", early=3)
        self.cli("wait", cid, check=3)
        reason = self.status(cid)["status"]["thumb"]["reason"]
        self.assertIn('built from "v1.0", which isn\'t a commit', reason)
        self.assertNotIn("--build v1.0", reason)

    def test_a_branch_name_in_build_commit_is_refused(self):
        # A branch (or @) names wherever the checkout is when the room
        # starts, not what was built: the gap this check is for.
        self.git("checkout", "-qb", "feature")
        for name in ["feature", "@", "HEAD"]:
            self.phone(f"echo BUILD_COMMIT={name}")
            cid = self.place("x", "--cast", "thumb", early=3)
            self.cli("wait", cid, check=3)
            reason = self.status(cid)["status"]["thumb"]["reason"]
            self.assertIn(f'built from "{name}", which isn\'t a commit', reason)
            self.cli("close", cid)

    def test_a_tag_is_resolved_and_a_refused_one_is_kept_as_its_commit(self):
        tagged = self.head()
        self.git("tag", "Release-1.2")  # capitals and all
        self.phone("echo BUILD_COMMIT=Release-1.2")
        cid = self.place("x", "--cast", "thumb")
        self.cli("wait", cid)
        s = self.status(cid)["status"]["thumb"]
        self.assertEqual(s["state"], "back", s)
        self.assertEqual(s["builds"][0]["commit"], tagged)
        self.cli("close", cid)
        write(os.path.join(self.project, "app.txt"), "two\n")
        self.git("commit", "-qam", "two")
        cid = self.place("x", "--cast", "thumb", early=3)
        self.cli("wait", cid, check=3)
        s = self.status(cid)["status"]["thumb"]
        self.assertIn(f"--build {tagged} to test", s["reason"])
        self.assertEqual(s["builds"][0]["commit"], tagged, s)

    def test_a_web_room_whose_work_changed_while_it_built_says_so(self):
        self.house()
        self.hire_file("member")
        hook = os.path.join(self.project, ".administration", "setup")
        write(hook, open(hook).read().replace("set -e\n", "set -e\necho changed-during-setup >> \"$1/app.txt\"\n"), 0o755)
        write(os.path.join(self.project, "app.txt"), "one\nwip\n")
        cid = self.place("x", "--cast", "member")
        self.cli("wait", cid)
        b = self.status(cid)["status"]["member"]["builds"][0]
        self.assertIn("the uncommitted work changed after the contract was placed", b["note"])
        self.assertNotIn("without the uncommitted work", b["note"])

    def test_a_screen_build_that_says_nothing_is_kept_as_not_reported(self):
        self.phone("true")
        write(os.path.join(self.project, ".administration", "roster.toml"), '''
            [equipment.phone]
            about = "A phone."
            setup = ".administration/equipment/phone/start"
            server = "screen"
            driver = ".administration/equipment/phone/server"
            ''')
        cid = self.place("x", "--cast", "thumb")
        self.cli("wait", cid)
        s = self.status(cid)["status"]["thumb"]
        self.assertEqual(s["builds"][0]["note"], "it didn't say which commit it was built from", s)

    def test_a_web_room_built_after_the_branch_moved_on_is_refused(self):
        self.house()
        self.hire_file("member")
        hook = os.path.join(self.project, ".administration", "setup")
        # The setup commits on the way (as a builder still at work would
        # have between place and the room): the room isn't the contract's.
        write(hook, open(hook).read().replace("set -e\n", "set -e\n[ -n \"$MOVE\" ] && (cd \"$1\" && echo moved > app.txt && git commit -qam moved)\n"), 0o755)
        self.env["MOVE"] = "1"
        rt = os.path.join(self.project, ".administration", "roster.toml")
        write(rt, open(rt).read().replace('pass_env = ["ROOM_DATA", "ROOM_ORIGIN"]',
                                          'pass_env = ["ROOM_DATA", "ROOM_ORIGIN", "MOVE", "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"]'))
        cid = self.place("x", "--cast", "member", check=None)
        cid = cid if isinstance(cid, str) else cid.stdout.split()[1]
        self.cli("wait", cid, check=3)
        self.assertIn("the project has moved on since the contract was placed", self.status(cid)["status"]["member"]["reason"])
        self.cli("close", cid)
        del self.env["MOVE"]
        cid = self.place("x", "--cast", "member")
        self.cli("wait", cid)
        s = self.status(cid)["status"]["member"]
        self.assertEqual(s["builds"], [{"from": "app", "commit": self.head()}], s)


    def test_equipment_hears_the_rooms_device_ports(self):
        # A development emulator on a room's console port crashed a room's
        # twice: rooms get a range of their own, which their setup claims in.
        seen = os.path.join(self.tmp, "ports")
        self.phone(f'echo "$ADMINISTRATION_DEVICE_PORTS" > "{seen}"')
        cid = self.place("x", "--cast", "thumb")
        self.cli("wait", cid)
        self.assertEqual(open(seen).read().strip(), "5620-5680")
        self.cli("close", cid)
        cid = self.place("x", "--cast", "thumb", env={"ADMINISTRATION_DEVICE_PORTS": "5700-5720"})
        self.cli("wait", cid)
        self.assertEqual(open(seen).read().strip(), "5700-5720")

    def test_equipment_check_says_which_build_it_installed(self):
        head = self.head()
        self.phone(f"echo BUILD_COMMIT={head}")
        out = self.cli("equipment-check", "phone").stdout
        self.assertIn(f"Its build: phone: {head[:10]}.", out)
        # A screen that says nothing is told how to (its picture fails here:
        # the stand-in driver draws nothing).
        self.phone("true")
        write(os.path.join(self.project, ".administration", "roster.toml"), '''
            [equipment.phone]
            about = "A phone."
            setup = ".administration/equipment/phone/start"
            server = "screen"
            driver = ".administration/equipment/phone/server"
            ''')
        self.assertIn("Have its setup print BUILD_COMMIT=", self.cli("equipment-check", "phone", check=2).stdout)

    def test_prune_clears_the_copy_with_the_rest_of_the_raw_files(self):
        self.hire_file("reader", kind="critic")
        cid = self.place("x", "--cast", "reader")
        self.cli("wait", cid)
        self.cli("close", cid)
        checkout = os.path.join(self.books, "contracts", cid, ".checkout")
        self.assertTrue(os.path.isdir(checkout))
        cut = checkout + "-1234"  # a copy killed before it was renamed into place
        write(os.path.join(cut, "app.txt"), "one\n")
        self.cli("prune", "--older", "0h")
        self.assertFalse(os.path.exists(checkout))
        self.assertFalse(os.path.exists(cut))


class BaseSanity(Base):
    """place --changes on a branch cut from the remote's main, while local
    main had unpushed fixes: the branch was tested without them."""

    def setUp(self):
        super().setUp()
        self.remote = os.path.join(self.tmp, "remote.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", self.remote], check=True)
        write(os.path.join(self.project, "app.txt"), "one\n")
        self.git("add", ".")
        self.git("commit", "-qm", "base")
        self.git("remote", "add", "origin", self.remote)
        self.git("push", "-q", "origin", "main")
        self.git("remote", "set-head", "origin", "main")
        self.hire_file("reader", kind="critic")

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.project, check=True, env=self.env, capture_output=True)

    def test_a_branch_missing_local_mains_commits_is_warned_about(self):
        # Local main gets fixes nobody pushed; the branch is cut from origin.
        write(os.path.join(self.project, "fix.txt"), "fixed\n")
        self.git("add", "fix.txt")
        self.git("commit", "-qm", "fix on local main")
        self.git("checkout", "-qb", "feature", "origin/main")
        write(os.path.join(self.project, "feature.txt"), "feature\n")
        self.git("add", "feature.txt")
        self.git("commit", "-qm", "feature")
        p = self.cli("place", "x", "--cast", "reader", "--changes")
        self.assertIn("this branch is missing 1 commit that main has", p.stderr)
        self.assertIn("local main is 1 commit ahead of origin/main, which the changes are compared with", p.stderr)
        self.assertIn("(--base main)", p.stderr)
        cid = p.stdout.split()[1]
        self.assertEqual(len(json.load(open(os.path.join(self.books, "contracts", cid, "contract.json")))["base_warnings"]), 2)
        self.cli("wait", cid)
        self.cli("close", cid)
        self.assertIn("- At place: this branch is missing 1 commit", self.cli("report", cid).stdout)

    def test_a_branch_on_top_of_local_main_says_nothing(self):
        self.git("checkout", "-qb", "feature")
        write(os.path.join(self.project, "feature.txt"), "feature\n")
        self.git("add", "feature.txt")
        self.git("commit", "-qm", "feature")
        p = self.cli("place", "x", "--cast", "reader", "--changes")
        self.assertNotIn("missing", p.stderr)
        self.assertNotIn("ahead", p.stderr)
        self.cli("wait", p.stdout.split()[1])


class BuildHook(Base):
    """The house builds what rooms install, once per commit, in a clean
    checkout with the house's untracked config: not one shared path built
    by hand from whichever checkout (and missing its .env)."""

    BUILD = """\
        #!/bin/sh
        # build COMMIT OUT: in a clean checkout, with the house's env/ in it.
        set -e
        echo "building $1 in $(pwd)" >&2
        echo built >> "$BUILD_COUNT"
        test "$1" = "$ADMINISTRATION_BUILD_COMMIT"
        { cat app.txt; cat .env; echo "work=$ADMINISTRATION_BUILD_WORK"; } > "$2/app.apk"
        echo "$2/app.apk"
        """

    def setUp(self):
        super().setUp()
        write(os.path.join(self.project, "app.txt"), "one\n")
        g = lambda *a: subprocess.run(["git", *a], cwd=self.project, check=True, env=self.env, capture_output=True)
        g("add", ".")
        g("commit", "-qm", "base")
        self.head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.project, check=True, capture_output=True, text=True).stdout.strip()
        adm = os.path.join(self.project, ".administration")
        self.count = os.path.join(self.tmp, "builds")
        self.env["BUILD_COUNT"] = self.count
        write(os.path.join(adm, "build"), self.BUILD, 0o755)
        write(os.path.join(adm, "env", ".env"), "MAPS_KEY=from-the-house\n")
        write(os.path.join(adm, "roster.toml"), '''
            [setup]
            pass_env = ["BUILD_COUNT"]
            [equipment.phone]
            about = "A phone."
            setup = ".administration/equipment/phone/start"
            server = ".administration/equipment/phone/server"
            tools = ["poke"]
            ''')
        write(os.path.join(adm, "equipment", "phone", "start"),
              '#!/bin/sh\ncp "$ADMINISTRATION_BUILD" "$ADMINISTRATION_ROOM/installed.apk"\necho "INSTALLED=$ADMINISTRATION_BUILD_COMMIT"\n', 0o755)
        write(os.path.join(adm, "equipment", "phone", "server"), "#!/bin/sh\nexit 0\n", 0o755)
        self.hire_file("thumb", extra="  browser: false\n  uses: [phone]")
        self.hire_file("pinky", extra="  browser: false\n  uses: [phone]")

    def test_each_room_installs_the_one_build_of_the_contracts_commit(self):
        cid = self.place("x", "--cast", "thumb,pinky")
        self.cli("wait", cid)
        rooms = []
        for name in ("thumb", "pinky"):
            s = self.status(cid)["status"][name]
            self.assertEqual(s["state"], "back", s)
            self.assertEqual(s["builds"], [{"from": "phone (build hook)", "commit": self.head}])
            rooms.append(s["room"])
            installed = open(os.path.join(s["room"], "installed.apk")).read()
            self.assertEqual(installed, "one\nMAPS_KEY=from-the-house\nwork=\n")  # the house's .env made it in
        self.assertEqual(open(self.count).read(), "built\n")  # built once for both rooms
        self.cli("close", cid)
        # The next contract on the same commit reuses it.
        cid = self.place("x", "--cast", "thumb")
        self.cli("wait", cid)
        self.assertEqual(open(self.count).read(), "built\n")
        # The checkout it was built in is gone; what it built is kept.
        out = self.cli("build").stdout
        self.assertIn(self.head[:10] + ": ", out)
        art = out.split(": ", 1)[1].strip()
        self.assertTrue(art.endswith("/out/app.apk"), art)
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(os.path.dirname(art)), "src")))

    def test_uncommitted_work_is_built_too_and_kept_apart(self):
        write(os.path.join(self.project, "app.txt"), "one\nwip\n")
        cid = self.place("x", "--cast", "thumb")
        self.cli("wait", cid)
        s = self.status(cid)["status"]["thumb"]
        self.assertEqual(s["state"], "back", s)
        installed = open(os.path.join(s["room"], "installed.apk")).read()
        self.assertTrue(installed.startswith("one\nwip\n"), installed)
        self.assertRegex(installed, r"work=work-[0-9a-f]{10}\n$")

    def test_a_build_that_fails_fails_the_room_with_its_log(self):
        write(os.path.join(self.project, ".administration", "build"), "#!/bin/sh\necho no sdk >&2\nexit 1\n", 0o755)
        cid = self.place("x", "--cast", "thumb", early=2)
        self.cli("wait", cid, check=2)
        reason = self.status(cid)["status"]["thumb"]["reason"]
        self.assertIn(f"the build of {self.head[:10]} failed", reason)
        log = reason.rsplit("see ", 1)[1]
        self.assertIn("no sdk", open(log).read())

    def test_prune_clears_failed_builds_and_a_killed_builds_checkout(self):
        write(os.path.join(self.project, ".administration", "build"), "#!/bin/sh\nexit 1\n", 0o755)
        self.cli("build", check=1)
        root = os.path.join(self.books, "builds")
        failed = [os.path.join(d, x) for d in [os.path.join(root, n) for n in os.listdir(root)] for x in os.listdir(d)]
        self.assertEqual(len(failed), 1)
        os.makedirs(os.path.join(failed[0], "src"))
        write(os.path.join(failed[0], "src", ".env"), "MAPS_KEY=x\n")  # as one killed outright leaves it
        self.cli("prune", "--older", "0h")
        self.assertFalse(os.path.exists(failed[0]))

    def test_a_build_left_in_the_checkout_is_refused(self):
        write(os.path.join(self.project, ".administration", "build"), "#!/bin/sh\necho x > app.apk\necho \"$(pwd)/app.apk\"\n", 0o755)
        self.assertIn("isn't in its out folder", self.cli("build", check=1).stderr)


class Restart(Base):
    """A crashed emulator came back when booted again: the screen restarts
    the device once before calling it lost."""

    def drive(self, driver, restart, n):
        lost = os.path.join(self.tmp, "equipment-lost")
        call = lambda i, name: {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": {}}}
        reqs = [call(i + 1, "screen_look") for i in range(n)]
        env = {**self.env, "ADMINISTRATION_EQUIPMENT_LOST": lost, "ADMINISTRATION_TAG": "abcd/thumb/pro"}
        if restart:
            env["ADMINISTRATION_EQUIPMENT_RESTART"] = restart
        p = subprocess.run([binary(), "_drive", driver, "emulator-5620"], input="".join(json.dumps(r) + "\n" for r in reqs),
                           capture_output=True, text=True, timeout=60, env=env)
        return {r["id"]: [c["text"] for c in r["result"]["content"] if c["type"] == "text"][0]
                for r in map(json.loads, p.stdout.splitlines())}, lost

    def test_a_dead_screen_is_restarted_once_then_lost(self):
        alive = os.path.join(self.tmp, "alive")
        png = os.path.join(self.tmp, "shot.png")
        # A one-pixel PNG.
        import base64, zlib, struct
        def chunk(t, d):
            return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
        open(png, "wb").write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
                              + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")) + chunk(b"IEND", b""))
        drv = os.path.join(self.tmp, "driver")
        write(drv, f'#!/bin/sh\nif [ -e "{alive}" ]; then cat "{png}"; else echo "device not found" >&2; exit 1; fi\n', 0o755)
        restart = os.path.join(self.tmp, "restart")
        tagged = os.path.join(self.tmp, "restart-tag")
        write(restart, f'#!/bin/sh\necho "$ADMINISTRATION_TAG" > "{tagged}"\ntouch "{alive}"\n', 0o755)
        text, lost = self.drive(drv, restart, 4)
        self.assertIn("has been restarted", text[3])
        self.assertNotIn("lost", text[3])
        self.assertIn("1x1", text[4])  # it answers again
        self.assertTrue(os.path.exists(lost + "-restarted"))
        self.assertFalse(os.path.exists(lost))
        self.assertEqual(open(tagged).read().strip(), "abcd/thumb/setup")  # the room's, cleared at teardown
        # Dead again after its one restart: lost.
        os.remove(alive)
        write(restart, '#!/bin/sh\nexit 0\n', 0o755)
        text, lost = self.drive(drv, restart, 3)
        self.assertIn("restarting it failed too: the screen still doesn't answer", text[3])
        self.assertIn("It's lost", text[3])

    def test_restart_is_only_for_a_screen(self):
        write(os.path.join(self.project, ".administration", "roster.toml"), '''
            [equipment.vm]
            about = "x"
            server = "commands"
            commands = [".administration/run"]
            restart = ".administration/run"
            ''')
        write(os.path.join(self.project, ".administration", "run"), "#!/bin/sh\n", 0o755)
        self.hire_file("thumb", extra="  browser: false\n  uses: [vm]")
        self.assertIn("restart is for a screen", self.cli("place", "x", "--cast", "thumb", check=1).stderr)


if __name__ == "__main__":
    unittest.main()
