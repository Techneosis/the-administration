package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"syscall"
	"time"
)

// The build hook (docs/design.md, The build hook): a project that lets the
// Administration build what its rooms install has .administration/build.
// It's run as the person, outside every wall, like setup, once per commit:
//
//	build <commit> <out>     in a clean checkout at the commit
//
// with the house's env/ folder (the untracked config a checkout lacks: a
// .env, a local.properties) copied in first, at the same paths. It puts
// what it built in <out> and prints its path as its last line (or prints
// nothing, and <out> is the build). The result is kept per commit in the
// books, so every room of a contract, and the next contract on the same
// commit, installs the same thing; equipment's setup gets it as
// ADMINISTRATION_BUILD, and the commit as ADMINISTRATION_BUILD_COMMIT.

// buildHook is the house's build hook, "" when it has none.
func buildHook(c *contract) string {
	hook := filepath.Join(contractHouse(c), "build")
	if st, err := os.Stat(hook); err == nil && !st.IsDir() && st.Mode()&0o111 != 0 {
		return hook
	}
	return ""
}

// buildsDir is where a project's builds are kept, one folder per commit.
func buildsDir(c *contract) (string, error) {
	books, err := booksDir()
	if err != nil {
		return "", err
	}
	key := contractKey(c)
	sum := sha256.Sum256([]byte(key))
	name := strings.Trim(nameSafe.ReplaceAllString(filepath.Base(key), "-"), "-")
	if name == "" {
		name = "project"
	}
	return filepath.Join(books, "builds", name+"-"+hex.EncodeToString(sum[:])[:12]), nil
}

var nameSafe = regexp.MustCompile(`[^A-Za-z0-9._-]+`)

func buildTimeout() time.Duration {
	if d, err := time.ParseDuration(os.Getenv("ADMINISTRATION_BUILD_TIMEOUT")); err == nil && d > 0 {
		return d
	}
	return time.Hour
}

// buildCommit is what a contract's build hook builds, as the build is kept:
// the commit chosen with --build, else HEAD's, with the uncommitted work's
// hash when there was some.
func buildCommit(c *contract) string {
	if c.Build != "" {
		return c.Build
	}
	return c.buildKey()
}

// ensureBuild is the build of the contract's commit: from the cache, or
// built now. Several rooms asking at once build it once; the others wait.
func ensureBuild(ctx context.Context, c *contract, hook string) (artifact, sha string, err error) {
	sha = buildCommit(c)
	if sha == "" {
		return "", "", errors.New("the house has a build hook, and the project has no commit to build")
	}
	root, err := buildsDir(c)
	if err != nil {
		return "", "", err
	}
	dir := filepath.Join(root, sha)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", "", err
	}
	if a, ok := builtAlready(dir); ok {
		touchBuild(dir)
		return a, sha, nil
	}
	unlock, err := lockWaiting(ctx, filepath.Join(dir, ".lock"))
	if err != nil {
		return "", "", err
	}
	defer unlock()
	if a, ok := builtAlready(dir); ok {
		return a, sha, nil // built while this one waited
	}
	a, err := runBuild(ctx, c, hook, sha, dir)
	return a, sha, err
}

// builtAlready is a kept build's artifact, if it's there and still exists.
func builtAlready(dir string) (string, bool) {
	b, err := os.ReadFile(filepath.Join(dir, "artifact"))
	if err != nil {
		return "", false
	}
	a := strings.TrimSpace(string(b))
	if _, err := os.Stat(a); err != nil {
		return "", false
	}
	return a, true
}

// lockWaiting takes an exclusive lock on path, waiting for it as long as
// ctx lets it.
func lockWaiting(ctx context.Context, path string) (func(), error) {
	for {
		if unlock, ok := tryLockFile(path); ok {
			return unlock, nil
		}
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case <-time.After(500 * time.Millisecond):
		}
	}
}

// runBuild builds sha into dir/out from a clean checkout in dir/src, which
// goes once it's done: what the hook keeps is what it put in out.
func runBuild(ctx context.Context, c *contract, hook, sha, dir string) (string, error) {
	src, out := filepath.Join(dir, "src"), filepath.Join(dir, "out")
	_ = os.RemoveAll(src)
	_ = os.RemoveAll(out)
	if err := os.MkdirAll(out, 0o700); err != nil {
		return "", err
	}
	if !isDir(c.Project) {
		return "", fmt.Errorf("the project (%s) is gone, so %s can't be built", c.Project, short(sha))
	}
	commit, uncommittedWork := c.Commit, c.Work
	if c.Build != "" {
		commit, uncommittedWork = c.Build, ""
	}
	work, err := makeCheckout(c.Project, commit, uncommittedWork, src)
	if err != nil {
		return "", err
	}
	defer os.RemoveAll(src)
	if err := copyHouseEnv(filepath.Join(contractHouse(c), "env"), work); err != nil {
		return "", err
	}
	logPath := filepath.Join(dir, "build.log")
	logf, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o600)
	if err != nil {
		return "", err
	}
	defer logf.Close()
	bctx, cancel := context.WithTimeout(ctx, buildTimeout())
	defer cancel()
	var stdout bytes.Buffer
	cmd := exec.Command(hook, commit, out)
	cmd.Dir = work
	cmd.Env = append(setupEnv(c.Project),
		"ADMINISTRATION_PROJECT="+c.Project,
		"ADMINISTRATION_HOUSE="+contractHouse(c),
		"ADMINISTRATION_SRC="+work,
		"ADMINISTRATION_BUILD_COMMIT="+commit,
		"ADMINISTRATION_BUILD_OUT="+out,
	)
	if uncommittedWork != "" {
		// The checkout has the uncommitted work on top: a build that
		// stamps its commit can say so.
		cmd.Env = append(cmd.Env, "ADMINISTRATION_BUILD_WORK="+uncommittedWork)
	}
	cmd.Stdout, cmd.Stderr = io.MultiWriter(&stdout, logf), logf
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	if err := cmd.Start(); err != nil {
		return "", fmt.Errorf("the build hook: %v", err)
	}
	if err := waitGroup(bctx, cmd); err != nil {
		if bctx.Err() == context.DeadlineExceeded {
			return "", fmt.Errorf("the build of %s took longer than %s (ADMINISTRATION_BUILD_TIMEOUT); see %s", short(sha), buildTimeout(), logPath)
		}
		if ctx.Err() != nil {
			return "", ctx.Err()
		}
		return "", fmt.Errorf("the build of %s failed (%v); see %s", short(sha), err, logPath)
	}
	artifact := out
	if ls := lines(stdout.String()); len(ls) > 0 {
		artifact = ls[len(ls)-1]
		if !filepath.IsAbs(artifact) {
			artifact = filepath.Join(out, artifact)
		}
	}
	artifact = filepath.Clean(artifact)
	if !within(artifact, out) && artifact != out {
		return "", fmt.Errorf("the build hook printed %s, which isn't in its out folder (%s): copy what it built there, as the checkout goes once it's done; see %s", artifact, out, logPath)
	}
	if _, err := os.Stat(artifact); err != nil {
		return "", fmt.Errorf("the build hook printed %s, and there's nothing there; see %s", artifact, logPath)
	}
	if err := os.WriteFile(filepath.Join(dir, "artifact"), []byte(artifact+"\n"), 0o600); err != nil {
		return "", err
	}
	return artifact, nil
}

// copyHouseEnv copies the house's env/ folder into a checkout, file by
// file at the same paths: the config a checkout of the commit doesn't have
// because nobody commits it. Links are refused, so what's copied is what's
// in the house.
func copyHouseEnv(from, to string) error {
	if !isDir(from) {
		return nil
	}
	return filepath.WalkDir(from, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(from, p)
		dst := filepath.Join(to, rel)
		switch {
		case d.IsDir():
			return os.MkdirAll(dst, 0o700)
		case d.Type()&fs.ModeSymlink != 0:
			return fmt.Errorf("the house's env/%s is a link; copy the file there instead", rel)
		case !d.Type().IsRegular():
			return nil
		}
		b, err := os.ReadFile(p)
		if err != nil {
			return err
		}
		st, _ := d.Info()
		return os.WriteFile(dst, b, st.Mode().Perm()|0o600)
	})
}

// cmdBuild is `administration build [REF]`: the build hook's build of a
// commit (HEAD by default), from the cache or made now. It's how a person
// tries the hook, or builds ahead of a contract.
func cmdBuild(args []string) error {
	f, err := parseFlags(args, map[string]bool{"project": true})
	if err != nil {
		return err
	}
	if len(f.pos) > 1 {
		return usageError{"build [REF] [--project DIR]"}
	}
	project, err := projectDir(f.get("project"))
	if err != nil {
		return err
	}
	c := &contract{Project: project, Key: projectKey(project), House: outsideHouse(project)}
	hook := buildHook(c)
	if hook == "" {
		return usageError{fmt.Sprintf("%s has no executable %s; see docs/design.md, The build hook", project, houseFile(project, "build"))}
	}
	ref := "HEAD"
	if len(f.pos) == 1 {
		ref = f.pos[0]
	}
	sha, err := git(project, "rev-parse", "--verify", "--quiet", ref+"^{commit}")
	if err != nil || sha == "" {
		return usageError{fmt.Sprintf("%s isn't a commit here", ref)}
	}
	c.Commit = sha
	if ref == "HEAD" {
		if w, err := workHash(project); err == nil && w != "" {
			c.Work = w
			fmt.Fprintf(os.Stderr, "administration: the work isn't all committed; building HEAD with it (%s), as a contract placed now would\n", w)
		}
	}
	a, built, err := ensureBuild(context.Background(), c, hook)
	if err != nil {
		return err
	}
	fmt.Printf("%s: %s\n", short(built), a)
	return nil
}

// touchBuild marks a kept build used now, so prune keeps it.
func touchBuild(dir string) {
	now := time.Now()
	_ = os.Chtimes(filepath.Join(dir, "artifact"), now, now)
}
