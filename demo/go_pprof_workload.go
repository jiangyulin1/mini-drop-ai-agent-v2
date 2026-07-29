// Mini-Drop Go pprof acceptance workload.
//
// It exposes net/http/pprof on localhost:6060 and continuously executes two
// recognizable CPU hotspots so the Agent can collect a real profile.
package main

import (
	"log"
	"net/http"
	_ "net/http/pprof"
	"os"
)

func fibonacci(n int) int {
	if n < 2 {
		return n
	}
	return fibonacci(n-1) + fibonacci(n-2)
}

func cpuHotspot() {
	for {
		_ = fibonacci(30)
	}
}

func main() {
	go cpuHotspot()
	address := os.Getenv("PPROF_ADDR")
	if address == "" {
		address = "127.0.0.1:6060"
	}
	log.Printf("Go pprof workload listening on %s", address)
	log.Fatal(http.ListenAndServe(address, nil))
}
