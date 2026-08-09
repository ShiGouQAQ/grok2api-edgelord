package egress

import (
	"context"
	"errors"
	"testing"

	domain "github.com/chenyme/grok2api/backend/internal/domain/egress"
	"github.com/chenyme/grok2api/backend/internal/repository"
)

// managedNodeRepository 是 Update/UpdateManyEnabled/Delete/DeleteMany 拦截测试
// 的内存仓库：仅实现 EgressRepository 与 BatchNodeDeleter 所需方法。
type managedNodeRepository struct {
	nodes []domain.Node
}

func (r *managedNodeRepository) GetEgressNode(_ context.Context, id uint64) (domain.Node, error) {
	for _, node := range r.nodes {
		if node.ID == id {
			return node, nil
		}
	}
	return domain.Node{}, repository.ErrNotFound
}

func (r *managedNodeRepository) ListEgressNodes(context.Context, domain.Scope, repository.SortQuery) ([]domain.Node, error) {
	return append([]domain.Node(nil), r.nodes...), nil
}

func (r *managedNodeRepository) CreateEgressNode(context.Context, domain.Node) (domain.Node, error) {
	return domain.Node{}, nil
}

func (r *managedNodeRepository) UpdateEgressNode(_ context.Context, value domain.Node) (domain.Node, error) {
	for index := range r.nodes {
		if r.nodes[index].ID == value.ID {
			r.nodes[index] = value
			return value, nil
		}
	}
	return domain.Node{}, repository.ErrNotFound
}

func (r *managedNodeRepository) DeleteEgressNode(_ context.Context, id uint64) error {
	for index := range r.nodes {
		if r.nodes[index].ID == id {
			r.nodes = append(r.nodes[:index], r.nodes[index+1:]...)
			return nil
		}
	}
	return repository.ErrNotFound
}

func (r *managedNodeRepository) DeleteEgressNodes(_ context.Context, ids []uint64) (int, error) {
	removed := 0
	for _, id := range ids {
		if err := r.DeleteEgressNode(context.Background(), id); err == nil {
			removed++
		}
	}
	return removed, nil
}

func (r *managedNodeRepository) ListEgressNodePage(_ context.Context, _ repository.EgressNodeListQuery) ([]domain.Node, int64, error) {
	return nil, 0, nil
}

func managedNodeForTest() *managedNodeRepository {
	return &managedNodeRepository{nodes: []domain.Node{
		{ID: 1, Name: "mihomo-sg-1", SourceKey: "mihomo:XAI-TEST-GROUP:mihomo-sg-1", Enabled: false},
		{ID: 2, Name: "普通节点", Enabled: true},
	}}
}

func TestManagedMihomoNodeRejectsUpdate(t *testing.T) {
	repo := managedNodeForTest()
	service := NewService(repo, nil, "")

	if _, err := service.Update(context.Background(), 1, Input{Name: "改名", Scope: domain.ScopeBuild}); !errors.Is(err, ErrManagedMihomoNode) {
		t.Fatalf("Update(mihomo synced) err=%v, want ErrManagedMihomoNode", err)
	}
	if _, err := service.Update(context.Background(), 2, Input{Name: "改名", Scope: domain.ScopeBuild}); err != nil {
		t.Fatalf("Update(regular) unexpected err=%v", err)
	}
}

func TestManagedMihomoNodeRejectsUpdateManyEnabled(t *testing.T) {
	repo := managedNodeForTest()
	service := NewService(repo, nil, "")

	if _, err := service.UpdateManyEnabled(context.Background(), []uint64{1}, true); !errors.Is(err, ErrManagedMihomoNode) {
		t.Fatalf("UpdateManyEnabled(mihomo synced) err=%v, want ErrManagedMihomoNode", err)
	}
	if _, err := service.UpdateManyEnabled(context.Background(), []uint64{2}, true); err != nil {
		t.Fatalf("UpdateManyEnabled(regular) unexpected err=%v", err)
	}
}

func TestManagedMihomoNodeRejectsDelete(t *testing.T) {
	repo := managedNodeForTest()
	service := NewService(repo, nil, "")

	if err := service.Delete(context.Background(), 1); !errors.Is(err, ErrManagedMihomoNode) {
		t.Fatalf("Delete(mihomo synced) err=%v, want ErrManagedMihomoNode", err)
	}
	if err := service.Delete(context.Background(), 2); err != nil {
		t.Fatalf("Delete(regular) unexpected err=%v", err)
	}
}

func TestManagedMihomoNodeRejectsDeleteMany(t *testing.T) {
	repo := managedNodeForTest()
	service := NewService(repo, nil, "")

	if _, err := service.DeleteMany(context.Background(), []uint64{1}); !errors.Is(err, ErrManagedMihomoNode) {
		t.Fatalf("DeleteMany(mihomo synced) err=%v, want ErrManagedMihomoNode", err)
	}
	if _, err := service.DeleteMany(context.Background(), []uint64{2}); err != nil {
		t.Fatalf("DeleteMany(regular) unexpected err=%v", err)
	}
}

func TestCreateSourceRejectsManagedName(t *testing.T) {
	repo, _, _ := managedSourceForTest()
	service := NewService(repo, nil, "")

	if _, err := service.CreateSource(context.Background(), SubscriptionSourceInput{
		Name: mihomoSyncSourceName, Scope: domain.ScopeBuild, Enabled: true,
	}); !errors.Is(err, ErrManagedSource) {
		t.Fatalf("CreateSource(managed name) err=%v, want ErrManagedSource", err)
	}
}
