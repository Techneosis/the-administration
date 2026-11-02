package main

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// Prune holds a build's lock while it removes the folder: one waiting for
// that lock starts the folder anew and locks the file there, not the one
// removed under it.
func TestABuildWaitingOnPruneLocksTheFolderAnew(t *testing.T) {
	d := filepath.Join(t.TempDir(), "build")
	if err := os.MkdirAll(d, 0o700); err != nil {
		t.Fatal(err)
	}
	lock := filepath.Join(d, ".lock")
	unprune, ok := tryLockFile(lock)
	if !ok {
		t.Fatal("no lock")
	}
	got := make(chan error, 1)
	var unlock func()
	go func() {
		var err error
		unlock, err = lockWaiting(context.Background(), lock)
		got <- err
	}()
	time.Sleep(200 * time.Millisecond)
	if err := os.RemoveAll(d); err != nil {
		t.Fatal(err)
	}
	unprune()
	select {
	case err := <-got:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("still waiting after the folder was removed")
	}
	defer unlock()
	if _, ok := tryLockFile(lock); ok {
		t.Fatal("the lock at the path isn't held: the waiter locked the removed file")
	}
}
