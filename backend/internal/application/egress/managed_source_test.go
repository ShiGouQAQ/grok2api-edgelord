package egress

import (
	"context"
	"errors"
	"testing"
	"time"

	domain "github.com/chenyme/grok2api/backend/internal/domain/egress"
	"github.com/chenyme/grok2api/backend/internal/repository"
)

// managedSourceRepository 内嵌 mihomoSyncRepositoryStub，补齐
// OperationsRepository 其余方法，构造可通过 NewService 注入的仓库。
type managedSourceRepository struct {
	*mihomoSyncRepositoryStub
}

func (r *managedSourceRepository) ListEgressSourcePage(_ context.Context, _ repository.EgressSourceListQuery) ([]domain.SubscriptionSource, int64, error) {
	return append([]domain.SubscriptionSource(nil), r.sources...), int64(len(r.sources)), nil
}

func (r *managedSourceRepository) GetEgressSource(_ context.Context, id uint64) (domain.SubscriptionSource, error) {
	for _, source := range r.sources {
		if source.ID == id {
			return source, nil
		}
	}
	return domain.SubscriptionSource{}, repository.ErrNotFound
}

func (r *managedSourceRepository) UpdateEgressSource(_ context.Context, value domain.SubscriptionSource) (domain.SubscriptionSource, error) {
	for index := range r.sources {
		if r.sources[index].ID == value.ID {
			r.sources[index] = value
			return value, nil
		}
	}
	return domain.SubscriptionSource{}, repository.ErrNotFound
}

func (r *managedSourceRepository) DeleteEgressSource(_ context.Context, id uint64) error {
	for index := range r.sources {
		if r.sources[index].ID == id {
			r.sources = append(r.sources[:index], r.sources[index+1:]...)
			return nil
		}
	}
	return repository.ErrNotFound
}

func (r *managedSourceRepository) ListDueEgressSources(context.Context, time.Time, int) ([]domain.SubscriptionSource, error) {
	return nil, nil
}

func (r *managedSourceRepository) UpdateEgressSourceSync(context.Context, uint64, time.Time, time.Time, int, string) error {
	return nil
}

func (r *managedSourceRepository) UpsertEgressNodesFromSource(context.Context, uint64, []domain.Node) (int, error) {
	return 0, nil
}

func (r *managedSourceRepository) UpdateEgressNodeProbe(context.Context, uint64, string, domain.ProbeResult) error {
	return nil
}

func (r *managedSourceRepository) ListDueEgressNodes(context.Context, time.Time, time.Duration, int) ([]domain.Node, error) {
	return nil, nil
}

func (r *managedSourceRepository) ListEgressNodePage(_ context.Context, _ repository.EgressNodeListQuery) ([]domain.Node, int64, error) {
	return nil, 0, nil
}

func (r *managedSourceRepository) GetEgressOperationsConfig(context.Context) (domain.OperationsConfig, error) {
	return domain.OperationsConfig{}, nil
}

func (r *managedSourceRepository) SaveEgressOperationsConfig(context.Context, domain.OperationsConfig) (domain.OperationsConfig, error) {
	return domain.OperationsConfig{}, nil
}

// managedSourceForTest 构造一个托管源与普通源各一的仓库。
func managedSourceForTest() (*managedSourceRepository, domain.SubscriptionSource, domain.SubscriptionSource) {
	repo := &managedSourceRepository{mihomoSyncRepositoryStub: &mihomoSyncRepositoryStub{}}
	managed, err := repo.CreateEgressSource(context.Background(), domain.SubscriptionSource{
		Name: mihomoSyncSourceName, Scope: domain.ScopeBuild, Enabled: false,
	})
	if err != nil {
		panic(err)
	}
	regular, err := repo.CreateEgressSource(context.Background(), domain.SubscriptionSource{
		Name: "机场订阅", Scope: domain.ScopeBuild, Enabled: true,
	})
	if err != nil {
		panic(err)
	}
	return repo, managed, regular
}

func TestManagedSourceRejectsUpdate(t *testing.T) {
	repo, managed, regular := managedSourceForTest()
	service := NewService(repo, nil, "")

	if _, err := service.UpdateSource(context.Background(), managed.ID, SubscriptionSourceInput{Name: "改名", Scope: domain.ScopeBuild}); !errors.Is(err, ErrManagedSource) {
		t.Fatalf("UpdateSource(managed) err=%v, want ErrManagedSource", err)
	}
	if _, err := service.UpdateSource(context.Background(), regular.ID, SubscriptionSourceInput{Name: "改名", Scope: domain.ScopeBuild}); err != nil {
		t.Fatalf("UpdateSource(regular) unexpected err=%v", err)
	}
}

func TestManagedSourceRejectsDelete(t *testing.T) {
	repo, managed, regular := managedSourceForTest()
	service := NewService(repo, nil, "")

	if err := service.DeleteSource(context.Background(), managed.ID); !errors.Is(err, ErrManagedSource) {
		t.Fatalf("DeleteSource(managed) err=%v, want ErrManagedSource", err)
	}
	if err := service.DeleteSource(context.Background(), regular.ID); err != nil {
		t.Fatalf("DeleteSource(regular) unexpected err=%v", err)
	}
}

func TestManagedSourceRejectsSync(t *testing.T) {
	repo, managed, _ := managedSourceForTest()
	service := NewService(repo, nil, "")

	if _, err := service.SyncSource(context.Background(), managed.ID); !errors.Is(err, ErrManagedSource) {
		t.Fatalf("SyncSource(managed) err=%v, want ErrManagedSource", err)
	}
}

func TestPublicSourceMarksManaged(t *testing.T) {
	repo, managed, _ := managedSourceForTest()
	service := NewService(repo, nil, "")

	values, _, err := service.ListSourcePage(context.Background(), 1, 10, "", SourceListFilter{})
	if err != nil {
		t.Fatal(err)
	}
	if len(values) != 2 {
		t.Fatalf("sources=%d, want 2", len(values))
	}
	for _, value := range values {
		if value.ID == managed.ID && !value.Managed {
			t.Fatal("managed source must be marked Managed")
		}
	}
}
