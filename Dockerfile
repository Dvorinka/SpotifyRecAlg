FROM golang:1.24-bookworm AS backend-build

WORKDIR /src/backend
COPY apps/backend/go.mod apps/backend/go.sum* ./
RUN go mod download

COPY apps/backend/ .
RUN go install github.com/pressly/goose/v3/cmd/goose@v3.24.3
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/recommendation-api ./cmd/api

FROM gcr.io/distroless/static-debian12:nonroot

WORKDIR /app

COPY --from=backend-build /out/recommendation-api /app/recommendation-api
COPY --from=backend-build /go/bin/goose /app/goose
COPY apps/backend/migrations /app/migrations
COPY apps/backend/docs /app/docs
COPY apps/web/index.html /app/web/index.html
COPY apps/web/app.js /app/web/app.js

EXPOSE 8080

ENTRYPOINT ["/app/recommendation-api"]
