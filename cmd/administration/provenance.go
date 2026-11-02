package main

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
)

// Provenance (docs/design.md, The build under test): which commit a
// contract is on, and which build each room actually ran. A contract
// records the project's HEAD when it's placed, and a hash of the
// uncommitted work on top of it when there is some. Every room's build is held to it: equipment says what it
// installed (BUILD_COMMIT=), a web room is built from the checkout, and a
// build that isn't the contract's is refused, unless the person placed it
// with --build on purpose. Critics and verifiers read a copy of the
// project at that commit, so a builder who keeps committing while the
// contract runs moves nothing under them.

// A room's build: where it came from (an equipment's name, "app" for the
// web room's setup, "build hook"), the commit it says it was built from,
// and the app's own name and version when the equipment gives them.
type roomBuild struct {
	From    string `json:"from"`
	Commit  string `json:"commit,omitempty"`
	App     string `json:"app,omitempty"`
	Version string `json:"version,omitempty"`
	// Why the build was let in although it isn't exactly the contract's
	// (built without the uncommitted work), or that it said nothing.
	Note string `json:"note,omitempty"`
	// Built with the contract's uncommitted work: the build hook's, or a web
	// room's from the checkout as placed.
	withWork bool
}

func (b roomBuild) String() string {
	s := b.From + ": "
	if b.Commit == "" {
		s += "build not reported"
	} else {
		s += short(b.Commit)
	}
	var id []string
	for _, p := range []string{b.App, b.Version} {
		if p != "" {
			id = append(id, p)
		}
	}
	if len(id) > 0 {
		s += " (" + strings.Join(id, " ") + ")"
	}
	if b.Note != "" {
		s += "; " + b.Note
	}
	return s
}

func short(sha string) string {
	if len(sha) > 10 && hexRE.MatchString(sha) {
		return sha[:10]
	}
	return sha
}

var hexRE = regexp.MustCompile(`^[0-9a-f]{7,64}$`)

// tested is the commit the contract is on: HEAD when it was placed. ""
// outside git. The uncommitted work on top of it, if any, is Work.
func (c *contract) tested() string { return c.Commit }

// buildKey is what a build of the contract is kept under: --build's commit,
// else HEAD's, with the uncommitted work's hash when there was some.
func (c *contract) buildKey() string {
	switch {
	case c.Build != "":
		return c.Build
	case c.Work != "":
		return c.Commit + "-" + c.Work
	}
	return c.Commit
}

// want is the commit a room's build must be: --build's, else HEAD's.
func (c *contract) want() string {
	if c.Build != "" {
		return c.Build
	}
	return c.Commit
}

// testedLine is the contract's build, for status and the summary.
func (c *contract) testedLine() string {
	switch {
	case c.Build != "" && c.Build != c.Commit:
		return fmt.Sprintf("Tested build: %s, chosen with --build (HEAD was %s)", short(c.Build), short(c.Commit))
	case c.Work != "":
		return fmt.Sprintf("Tested build: %s plus uncommitted work (%s)", short(c.Commit), c.Work)
	case c.Commit != "":
		return "Tested build: " + short(c.Commit)
	}
	return ""
}

// recordCommit notes what the contract is on: HEAD, a hash of any
// uncommitted work, and --build's commit if the person chose one. A project
// outside git (or with no commit yet) records nothing and checks nothing.
func recordCommit(c *contract, build string) error {
	head, err := git(c.Project, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
	if err != nil || head == "" {
		if build != "" {
			return usageError{"--build: the project has no commits to hold a build to"}
		}
		return nil
	}
	c.Commit = head
	c.Work, _ = workHash(c.Project)
	if build != "" {
		full, err := git(c.Project, "rev-parse", "--verify", "--quiet", build+"^{commit}")
		switch {
		case err == nil && full != "":
			c.Build = full
		case hexRE.MatchString(strings.ToLower(build)):
			// A build from a commit this checkout doesn't have (someone
			// else's): held to the letters given.
			c.Build = strings.ToLower(build)
		default:
			return usageError{fmt.Sprintf("--build %s: not a commit here, nor a commit's hash", build)}
		}
	}
	return nil
}

// uncommitted is the project's files that differ from HEAD: changed,
// removed, and new ones not ignored, never secrets.
func uncommitted(project string) (changed, untracked []string, err error) {
	out, err := git(project, append([]string{"diff", "--no-ext-diff", "--no-textconv", "--name-only", "HEAD", "--", "."}, secretPathspecs...)...)
	if err != nil {
		return nil, nil, err
	}
	u, err := git(project, append([]string{"ls-files", "--others", "--exclude-standard", "--", "."}, secretPathspecs...)...)
	if err != nil {
		return nil, nil, err
	}
	return lines(out), lines(u), nil
}

// workHash names the uncommitted work as it is now: a hash of every
// uncommitted file's name and contents (a removed one as removed), "" when
// there's none. It's read only, as everything the house does in git is: the
// work isn't committed anywhere, so a build or a copy of it is made from
// the files, and this says whether they're still the ones placed.
func workHash(project string) (string, error) {
	changed, untracked, err := uncommitted(project)
	if err != nil || len(changed)+len(untracked) == 0 {
		return "", err
	}
	top, err := git(project, "rev-parse", "--show-toplevel")
	if err != nil {
		return "", err
	}
	// diff --name-only names files from the top of the repository;
	// ls-files, from the project.
	h := sha256.New()
	add := func(base, f string) {
		fmt.Fprintf(h, "%s\x00", f)
		if b, err := os.ReadFile(filepath.Join(base, f)); err == nil {
			h.Write(b)
		} else {
			h.Write([]byte("\x00removed"))
		}
		h.Write([]byte{0})
	}
	for _, f := range changed {
		add(top, f)
	}
	for _, f := range untracked {
		add(project, f)
	}
	return "work-" + hex.EncodeToString(h.Sum(nil))[:10], nil
}

// overlayWork copies the project's uncommitted work onto a copy of it at
// HEAD (dest is the copy of the repository's top), and says if the work
// isn't the one the contract was placed with any more.
func overlayWork(project, dest, want string) error {
	if got, err := workHash(project); err != nil {
		return err
	} else if got != want {
		return fmt.Errorf("the uncommitted work has changed since the contract was placed (%s, now %s): commit it, or place the contract again", want, orNone(got))
	}
	changed, untracked, err := uncommitted(project)
	if err != nil {
		return err
	}
	top, err := git(project, "rev-parse", "--show-toplevel")
	if err != nil {
		return err
	}
	rel, _ := filepath.Rel(realPath(top), realPath(project))
	cp := func(src, dst string) error {
		st, err := os.Lstat(src)
		if err != nil {
			_ = os.Remove(dst) // removed in the work
			return nil
		}
		if !st.Mode().IsRegular() {
			return nil // a link or a folder: not copied out of the project
		}
		b, err := os.ReadFile(src)
		if err != nil {
			return err
		}
		if err := os.MkdirAll(filepath.Dir(dst), 0o700); err != nil {
			return err
		}
		return os.WriteFile(dst, b, st.Mode().Perm()|0o600)
	}
	for _, f := range changed {
		if err := cp(filepath.Join(top, f), filepath.Join(dest, f)); err != nil {
			return err
		}
	}
	for _, f := range untracked {
		if err := cp(filepath.Join(project, f), filepath.Join(dest, rel, f)); err != nil {
			return err
		}
	}
	return nil
}

func orNone(s string) string {
	if s == "" {
		return "none"
	}
	return s
}

// makeCheckout is a plain copy of the project at sha, in dest: a clone
// that borrows the project's objects, checked out, its .git then removed,
// so what's left is the files and nothing that points back; with work
// (the hash of the uncommitted work), that work copied on top. The
// project's own repository is only read. A project in a subfolder of its
// repository is that subfolder of the copy. It returns the folder to read.
func makeCheckout(project, sha, work, dest string) (string, error) {
	top, err := git(project, "rev-parse", "--show-toplevel")
	if err != nil {
		return "", err
	}
	if err := os.MkdirAll(filepath.Dir(dest), 0o700); err != nil {
		return "", err
	}
	for _, args := range [][]string{
		{"clone", "--quiet", "--shared", "--no-checkout", top, dest},
		{"-C", dest, "-c", "advice.detachedHead=false", "checkout", "--quiet", "--detach", sha},
	} {
		if out, err := exec.Command("git", append(append([]string{}, gitSafe...), args...)...).CombinedOutput(); err != nil {
			_ = os.RemoveAll(dest)
			return "", fmt.Errorf("a checkout at %s: %v: %s", short(sha), err, strings.TrimSpace(string(out)))
		}
	}
	_ = os.RemoveAll(filepath.Join(dest, ".git"))
	if work != "" {
		if err := overlayWork(project, dest, work); err != nil {
			_ = os.RemoveAll(dest)
			return "", err
		}
	}
	rel, err := filepath.Rel(realPath(top), realPath(project))
	if err != nil || strings.HasPrefix(rel, "..") {
		rel = "."
	}
	return filepath.Join(dest, rel), nil
}

// checkoutDir is where a contract keeps its copy of the project.
func checkoutDir(dir string) string { return filepath.Join(dir, ".checkout") }

// projectCopy is the folder critics and verifiers read as the project: the
// contract's checkout at its tested commit, made now if it isn't there yet
// (a contract placed before it was kept), or the project itself when there
// is no commit to copy, or it can't be had (said in why).
func projectCopy(c *contract, dir string) (path, why string) {
	if c.Checkout != "" && isDir(c.Checkout) {
		return c.Checkout, ""
	}
	sha := c.tested()
	if sha == "" {
		return c.Project, ""
	}
	if !isDir(c.Project) {
		return c.Project, "the project is gone, so there's no copy of it at " + short(sha)
	}
	note := ""
	work := c.Work
	if work != "" {
		if now, _ := workHash(c.Project); now != work {
			// The work placed is gone (committed, changed): the commit is
			// still the nearest thing to what was tested.
			work, note = "", fmt.Sprintf("the uncommitted work the contract was placed with (%s) has changed since, so the copy at %s is without it", c.Work, short(sha))
		}
	}
	p, err := makeCheckout(c.Project, sha, work, checkoutDir(dir))
	if err != nil {
		return c.Project, err.Error() + "; reading the project as it is now"
	}
	c.Checkout = p
	_ = writeJSON(filepath.Join(dir, "contract.json"), c)
	return p, note
}

// checkBuild holds what a room says it runs to the contract's commit. A
// build that names nothing is let in and said so; one from another commit
// is refused, with how to test it on purpose. A build of HEAD when the
// contract also has uncommitted work is let in with a note: the work under
// review may not all be in it.
func checkBuild(c *contract, b roomBuild) (roomBuild, error) {
	got := strings.ToLower(strings.TrimSpace(b.Commit))
	dirty := false
	for _, suf := range []string{"-dirty", "+dirty", "-modified"} {
		if strings.HasSuffix(got, suf) {
			got, dirty = strings.TrimSuffix(got, suf), true
		}
	}
	b.Commit = got
	want := c.want()
	if got == "" {
		if want != "" {
			b.Note = "it didn't say which commit it was built from"
		}
		return b, nil
	}
	if want == "" {
		return b, nil
	}
	full := got
	if hexRE.MatchString(got) {
		if r, err := git(c.Project, "rev-parse", "--verify", "--quiet", got+"^{commit}"); err == nil && r != "" {
			full = r
		}
	}
	same := func(sha string) bool {
		return sha != "" && (full == sha || len(got) >= 7 && strings.HasPrefix(sha, got))
	}
	switch {
	case same(want):
		b.Commit = full
		if c.Build == "" && c.Work != "" && !b.withWork {
			if dirty {
				b.Note = "built with uncommitted work, which may not be the work under contract"
			} else {
				b.Note = "built from " + short(c.Commit) + " without the uncommitted work"
			}
		}
		return b, nil
	}
	what := "the contract is on " + short(want) + " (HEAD when it was placed)"
	if c.Build != "" {
		what = "the contract was placed to test " + short(want) + " (--build)"
	}
	return b, refusal{fmt.Sprintf("%s runs a build of %s, and %s: rebuild it from %s, or place the contract with --build %s to test that build on purpose",
		b.From, short(got), what, short(want), short(got))}
}

// buildFromVars is the build an equipment's setup reported.
func buildFromVars(name string, v map[string]string) roomBuild {
	return roomBuild{From: name, Commit: v["BUILD_COMMIT"], App: v["BUILD_APP"], Version: v["BUILD_VERSION"]}
}

// addBuild records a room's build in its status, once per source.
func addBuild(s *status, b roomBuild) {
	for i, o := range s.Builds {
		if o.From == b.From {
			s.Builds[i] = b
			return
		}
	}
	s.Builds = append(s.Builds, b)
}

// provenanceNote is the summary's word on the build: the commit the
// contract is on, what each room ran, and what place warned about the
// base. "" for a contract with nothing to say (outside git).
func provenanceNote(c *contract, dir string) string {
	var b strings.Builder
	if t := c.testedLine(); t != "" {
		b.WriteString(t + ".")
		if c.Checkout != "" {
			b.WriteString(" Critics and verifiers read the project as it was then, not as it is now.")
		}
		b.WriteString("\n")
	}
	for _, p := range c.Professionals {
		for _, rb := range readStatus(dir, p.Name).Builds {
			fmt.Fprintf(&b, "- %s's room ran %s\n", p.Name, rb)
		}
	}
	for _, w := range c.BaseWarnings {
		fmt.Fprintf(&b, "- At place: %s\n", w)
	}
	if b.Len() > 0 {
		b.WriteString("\n")
	}
	return b.String()
}
