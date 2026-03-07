package main

import (
	"fmt"
	"os"

	"github.com/spf13/cobra"
)

func main() {
	if err := newRootCmd().Execute(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func newRootCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "coara-cli",
		Short: "coara CLI - ソースコード特化 RAG 取り込みツール",
	}

	// TODO: サブコマンドを追加する
	// cmd.AddCommand(newIndexCmd())
	// cmd.AddCommand(newStatusCmd())

	return cmd
}
