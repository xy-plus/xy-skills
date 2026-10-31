LAYOUT_COLLISION_FIXTURE = {
    "files": {
        "docs/layout-first.md": "Layout collision fixture first card.\n",
        "docs/layout-tall.md": "Layout collision fixture tall card.\n",
        "docs/layout-leaf.md": "Layout collision fixture leaf.\n",
    },
    "nodes": """    aaa_layout_a = node '前序分支' {
      description '在展开布局中先出现的分支。'
      branch = node '分支节点' {
        description '包含一个前序卡片和一张更深、更高的卡片。'
        first = node '前序卡片' {
          description '先占据同列较高位置。'
          link ../docs/layout-first.md 'docs'
        }
        tall = node '更深更高卡片' {
          description '用于覆盖多层首子节点在同列碰撞时的布局情况。这个说明故意写得足够长，用来撑高前序分支中的卡片，并让后续分支中的首子链发生真实的同列碰撞。布局需要在保留祖先中线对齐的同时避让这张卡片。继续填充文本以稳定触发该场景。这里的长内容只用于测试布局，不代表任何真实产品文案，也不改变节点之间的组成关系。'
          deeper = node '更深一层' {
            description '前序分支还继续向下。'
            link ../docs/layout-tall.md 'docs'
          }
        }
      }
    }
    bbb_layout_b = node '后序分支' {
      description '该分支位于前序分支之后。'
      child = node '首子节点' {
        description '沿首个下级继续。'
        leaf = node '首子链末端' {
          description '此节点与前序分支中的高卡片相撞。'
          link ../docs/layout-leaf.md 'docs'
        }
      }
    }
""",
}
