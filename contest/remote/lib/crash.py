#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import re
import unittest


def has_crash(output):
    return output.find("] RIP: ") != -1 or \
           output.find("] Call Trace:") != -1 or \
           output.find('] ref_tracker: ') != -1 or \
           output.find('unreferenced object 0x') != -1


def finger_print_skip_pfx_len(filters, needles):
    # Filter may contain a list of needles we want to skip
    # Assume it's well sorted, so we don't need LPM...
    if filters and 'crash-prefix-skip' in filters:
        for skip_pfx in filters['crash-prefix-skip']:
            if len(needles) < len(skip_pfx):
                continue
            if needles[:len(skip_pfx)] == skip_pfx:
                return len(skip_pfx)
    return 0


def crash_finger_print(filters, lines):
    needles = []
    need_re = re.compile(r'.*(  |0:|>\] )([a-z0-9_]+)\+0x[0-9a-f]+/0x[0-9a-f]+.*')
    skip = 0
    for line in lines:
        m = need_re.match(line)
        if not m:
            continue
        needles.append(m.groups()[1])
        skip = finger_print_skip_pfx_len(filters, needles)
        if len(needles) - skip == 5:
            break

    needles = needles[skip:]
    return ":".join(needles)


def extract_crash(outputs, prompt, get_filters):
    in_crash = False
    start = 0
    crash_lines = []
    finger_prints = set()
    last5 = [""] * 5
    outputs = outputs.split('\n')
    for line in outputs:
        if in_crash:
            in_crash &= '] ---[ end trace ' not in line
            in_crash &= ']  </TASK>' not in line
            in_crash &= line[-2:] != '] '
            in_crash &= not line.startswith(prompt)
            if not in_crash:
                finger_prints.add(crash_finger_print(get_filters(),
                                                     crash_lines[start:]))
        else:
            in_crash |= '] Hardware name: ' in line
            in_crash |= '] ref_tracker: ' in line
            in_crash |= ' blocked for more than ' in line
            in_crash |= line.startswith('unreferenced object 0x')
            if in_crash:
                start = len(crash_lines)
                crash_lines += last5

        # Keep last 5 to get some of the stuff before stack trace
        last5 = last5[1:] + ["| " + line]

        if in_crash:
            crash_lines.append(line)

    return crash_lines, finger_prints


#############################################################
# END OF CODE --- START OF UNIT TESTS
#############################################################


class TestCrashes(unittest.TestCase):
    def test_memleak(self):
        self.assertTrue(has_crash(TestCrashes.kmemleak))
        lines, fingers = extract_crash(TestCrashes.kmemleak, "xx__->", lambda : None)
        self.assertGreater(len(lines), 8)
        self.assertEqual(fingers,
                         {'kmalloc_trace_noprof:tcp_ao_alloc_info:do_tcp_setsockopt:do_sock_setsockopt:__sys_setsockopt'})

    def test_bad_irq(self):
        self.assertTrue(has_crash(TestCrashes.bad_irq))
        lines, fingers = extract_crash(TestCrashes.bad_irq, "xx__->", lambda : None)
        self.assertGreater(len(lines), 10)
        self.assertEqual(fingers,
                         {'dump_stack_lvl:__report_bad_irq:note_interrupt:handle_irq_event:handle_edge_irq'})

    def test_bad_irq_trim(self):
        self.assertTrue(has_crash(TestCrashes.bad_irq))
        lines, fingers = extract_crash(TestCrashes.bad_irq, "xx__->",
                                       lambda : {'crash-prefix-skip': [["dump_stack_lvl","__report_bad_irq"]]})
        self.assertGreater(len(lines), 10)
        self.assertEqual(fingers,
                         {'note_interrupt:handle_irq_event:handle_edge_irq:__common_interrupt:common_interrupt'})

    def test_refleak(self):
        self.assertTrue(has_crash(TestCrashes.refleak))
        lines, fingers = extract_crash(TestCrashes.refleak, "xx__->", lambda : None)
        self.assertGreater(len(lines), 50)
        self.assertEqual(fingers,
                         {'dev_hard_start_xmit:__dev_queue_xmit:ip6_finish_output2:ip6_finish_output:netdev_get_by_index',
                          '___sys_sendmsg:__sys_sendmsg:do_syscall_64:dst_init:dst_alloc',
                          'dst_init:dst_alloc:ip6_dst_alloc:ip6_rt_pcpu_alloc:ip6_pol_route',
                          '___sys_sendmsg:__sys_sendmsg:do_syscall_64:ipv6_add_dev:addrconf_notify',
                          'dev_hard_start_xmit:__dev_queue_xmit:arp_solicit:neigh_probe:dst_init'})

    def test_hung_task(self):
        self.assertTrue(has_crash(TestCrashes.hung_task))
        lines, fingers = extract_crash(TestCrashes.hung_task, "xx__->", lambda : None)
        self.assertGreater(len(lines), 10)
        self.assertEqual(fingers,
                         {'__schedule:schedule:__wait_on_freeing_inode:find_inode_fast:iget_locked',
                          '__schedule:schedule:d_alloc_parallel:__lookup_slow:walk_component'})

    #########################################################
    ### Sample outputs
    #########################################################
    kmemleak = """xx__-> echo $?
0
xx__-> echo scan > /sys/kernel/debug/kmemleak && cat /sys/kernel/debug/kmemleak
unreferenced object 0xffff888003692380 (size 128):
  comm "unsigned-md5_ip", pid 762, jiffies 4294831244
  hex dump (first 32 bytes):
    00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00  ................
    00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00  ................
  backtrace (crc 2128895f):
    [<ffffffffb2131db6>] kmalloc_trace_noprof+0x236/0x290
    [<ffffffffb3dee5e4>] tcp_ao_alloc_info+0x44/0xf0
    [<ffffffffb3df0263>] tcp_ao_info_cmd.constprop.0+0x423/0x8e0
    [<ffffffffb3c2a534>] do_tcp_setsockopt+0xa64/0x2320
    [<ffffffffb38e3629>] do_sock_setsockopt+0x149/0x3a0
    [<ffffffffb38ee8b4>] __sys_setsockopt+0x104/0x1a0
    [<ffffffffb38eea1d>] __x64_sys_setsockopt+0xbd/0x160
    [<ffffffffb41488c1>] do_syscall_64+0xc1/0x1d0
    [<ffffffffb4200130>] entry_SYSCALL_64_after_hwframe+0x77/0x7f
xx__-> 
    """

    bad_irq = """[ 1000.092583][ T3849] tc (3849) used greatest stack depth: 23216 bytes left
[ 1081.418714][    C3] irq 4: nobody cared (try booting with the "irqpoll" option)
[ 1081.419111][    C3] CPU: 3 PID: 3703 Comm: perl Not tainted 6.10.0-rc3-virtme #1
[ 1081.419389][    C3] Hardware name: QEMU Standard PC (i440FX + PIIX, 1996), BIOS rel-1.16.3-0-ga6ed6b701f0a-prebuilt.qemu.org 04/01/2014
[ 1081.419773][    C3] Call Trace:
[ 1081.419909][    C3]  <IRQ>
[ 1081.420008][    C3]  dump_stack_lvl+0x82/0xd0
[ 1081.420197][    C3]  __report_bad_irq+0x5f/0x180
[ 1081.420371][    C3]  note_interrupt+0x6b3/0x860
[ 1081.420556][    C3]  handle_irq_event+0x16d/0x1c0
[ 1081.420728][    C3]  handle_edge_irq+0x1fa/0xb60
[ 1081.420912][    C3]  __common_interrupt+0x82/0x170
[ 1081.421128][    C3]  common_interrupt+0x7e/0x90
[ 1081.421330][    C3]  </IRQ>
[ 1081.421430][    C3]  <TASK>
[ 1081.421526][    C3]  asm_common_interrupt+0x26/0x40
[ 1081.421711][    C3] RIP: 0010:_raw_spin_unlock_irqrestore+0x43/0x70
[ 1081.421951][    C3] Code: 10 e8 d1 1a 92 fd 48 89 ef e8 49 8b 92 fd 81 e3 00 02 00 00 75 1d 9c 58 f6 c4 02 75 29 48 85 db 74 01 fb 65 ff 0d 95 7a 06 54 <74> 0e 5b 5d c3 cc cc cc cc e8 7f 01 b6 fd eb dc 0f 1f 44 00 00 5b
[ 1081.422616][    C3] RSP: 0018:ffffc90000bdfac0 EFLAGS: 00000286
[ 1081.422862][    C3] RAX: 0000000000000006 RBX: 0000000000000200 RCX: 1ffffffff5e2ff1a
[ 1081.423147][    C3] RDX: 0000000000000000 RSI: 0000000000000000 RDI: ffffffffabfd4d81
[ 1081.423422][    C3] RBP: ffffffffafa41060 R08: 0000000000000001 R09: fffffbfff5e2b0a8
[ 1081.423701][    C3] R10: ffffffffaf158547 R11: 0000000000000000 R12: 0000000000000001
[ 1081.423991][    C3] R13: 0000000000000286 R14: ffffffffafa41060 R15: ffff888006683800
[ 1081.424296][    C3]  ? _raw_spin_unlock_irqrestore+0x51/0x70
[ 1081.424542][    C3]  uart_write+0x13d/0x330
[ 1081.424695][    C3]  process_output_block+0x13e/0x790
[ 1081.424885][    C3]  ? lockdep_hardirqs_on_prepare+0x275/0x410
[ 1081.425144][    C3]  n_tty_write+0x412/0x7a0
[ 1081.425344][    C3]  ? __pfx_n_tty_write+0x10/0x10
[ 1081.425535][    C3]  ? trace_lock_acquire+0x14d/0x1f0
[ 1081.425722][    C3]  ? __pfx_woken_wake_function+0x10/0x10
[ 1081.425909][    C3]  ? iterate_tty_write+0x95/0x540
[ 1081.426098][    C3]  ? lock_acquire+0x32/0xc0
[ 1081.426285][    C3]  ? iterate_tty_write+0x95/0x540
[ 1081.426473][    C3]  iterate_tty_write+0x228/0x540
[ 1081.426660][    C3]  ? tty_ldisc_ref_wait+0x28/0x80
[ 1081.426850][    C3]  file_tty_write.constprop.0+0x1db/0x370
[ 1081.427037][    C3]  vfs_write+0xa18/0x10b0
[ 1081.427184][    C3]  ? __pfx_lock_acquire.part.0+0x10/0x10
[ 1081.427369][    C3]  ? __pfx_vfs_write+0x10/0x10
[ 1081.427557][    C3]  ? clockevents_program_event+0xf6/0x300
[ 1081.427750][    C3]  ? __fget_light+0x53/0x1e0
[ 1081.427938][    C3]  ? clockevents_program_event+0x1ea/0x300
[ 1081.428170][    C3]  ksys_write+0xf5/0x1e0
[ 1081.428319][    C3]  ? __pfx_ksys_write+0x10/0x10
[ 1081.428515][    C3]  do_syscall_64+0xc1/0x1d0
[ 1081.428696][    C3]  entry_SYSCALL_64_after_hwframe+0x77/0x7f
[ 1081.428915][    C3] RIP: 0033:0x7f3d90a53957
[ 1081.429131][    C3] Code: 0b 00 f7 d8 64 89 02 48 c7 c0 ff ff ff ff eb b7 0f 1f 00 f3 0f 1e fa 64 8b 04 25 18 00 00 00 85 c0 75 10 b8 01 00 00 00 0f 05 <48> 3d 00 f0 ff ff 77 51 c3 48 83 ec 28 48 89 54 24 18 48 89 74 24
[ 1081.429726][    C3] RSP: 002b:00007ffe774784d8 EFLAGS: 00000246 ORIG_RAX: 0000000000000001
[ 1081.429987][    C3] RAX: ffffffffffffffda RBX: 00005596b8d2a1d0 RCX: 00007f3d90a53957
[ 1081.430242][    C3] RDX: 0000000000000001 RSI: 00005596b8d2a1d0 RDI: 0000000000000001
[ 1081.430494][    C3] RBP: 0000000000000001 R08: 0000000000000000 R09: 0000000000002000
[ 1081.430753][    C3] R10: 0000000000000001 R11: 0000000000000246 R12: 00005596b8d165c0
[ 1081.431012][    C3] R13: 00005596b8cf72a0 R14: 0000000000000001 R15: 00005596b8d165c0
[ 1081.431290][    C3]  </TASK>
[ 1081.431421][    C3] handlers:
[ 1081.431553][    C3] [<ffffffffaa8f7450>] serial8250_interrupt
[ 1081.432206][    C3] Disabling IRQ #4
"""

    refleak = """
[ 1055.837009][   T75] veth_A-C: left allmulticast mode
[ 1055.837273][   T75] veth_A-C: left promiscuous mode
[ 1055.837697][   T75] br0: port 1(veth_A-C) entered disabled state
[ 1619.761346][T10781] Initializing XFRM netlink socket
[ 1868.101248][T12484] unregister_netdevice: waiting for veth_A-R1 to become free. Usage count = 5
[ 1868.101753][T12484] ref_tracker: veth_A-R1@ffff8880060c45e0 has 1/4 users at
[ 1868.101753][T12484]      dst_init+0x84/0x4a0
[ 1868.101753][T12484]      dst_alloc+0x97/0x150
[ 1868.101753][T12484]      ip6_dst_alloc+0x23/0x90
[ 1868.101753][T12484]      ip6_rt_pcpu_alloc+0x1e6/0x520
[ 1868.101753][T12484]      ip6_pol_route+0x56f/0x840
[ 1868.101753][T12484]      fib6_rule_lookup+0x334/0x630
[ 1868.101753][T12484]      ip6_route_output_flags+0x259/0x480
[ 1868.101753][T12484]      ip6_dst_lookup_tail.constprop.0+0x700/0xb60
[ 1868.101753][T12484]      ip6_dst_lookup_flow+0x88/0x190
[ 1868.101753][T12484]      udp_tunnel6_dst_lookup+0x2b0/0x4d0
[ 1868.101753][T12484]      vxlan_xmit_one+0xd41/0x4500 [vxlan]
[ 1868.101753][T12484]      vxlan_xmit+0x9b6/0xf10 [vxlan]
[ 1868.101753][T12484]      dev_hard_start_xmit+0x10e/0x360
[ 1868.101753][T12484]      __dev_queue_xmit+0xe76/0x1740
[ 1868.101753][T12484]      arp_solicit+0x4aa/0xe20
[ 1868.101753][T12484]      neigh_probe+0xaa/0xf0
[ 1868.101753][T12484] 
[ 1868.104788][T12484] ref_tracker: veth_A-R1@ffff8880060c45e0 has 1/4 users at
[ 1868.104788][T12484]      dst_init+0x84/0x4a0
[ 1868.104788][T12484]      dst_alloc+0x97/0x150
[ 1868.104788][T12484]      ip6_dst_alloc+0x23/0x90
[ 1868.104788][T12484]      ip6_rt_pcpu_alloc+0x1e6/0x520
[ 1868.104788][T12484]      ip6_pol_route+0x56f/0x840
[ 1868.104788][T12484]      fib6_rule_lookup+0x334/0x630
[ 1868.104788][T12484]      ip6_route_output_flags+0x259/0x480
[ 1868.104788][T12484]      ip6_dst_lookup_tail.constprop.0+0x700/0xb60
[ 1868.104788][T12484]      ip6_dst_lookup_flow+0x88/0x190
[ 1868.104788][T12484]      udp_tunnel6_dst_lookup+0x2b0/0x4d0
[ 1868.104788][T12484]      vxlan_xmit_one+0xd41/0x4500 [vxlan]
[ 1868.104788][T12484]      vxlan_xmit+0x9b6/0xf10 [vxlan]
[ 1868.104788][T12484]      dev_hard_start_xmit+0x10e/0x360
[ 1868.104788][T12484]      __dev_queue_xmit+0xe76/0x1740
[ 1868.104788][T12484]      ip6_finish_output2+0x59b/0xff0
[ 1868.104788][T12484]      ip6_finish_output+0x553/0xdf0
[ 1868.104788][T12484] 
[ 1868.107473][T12484] ref_tracker: veth_A-R1@ffff8880060c45e0 has 1/4 users at
[ 1868.107473][T12484]      netdev_get_by_index+0x5e/0x80
[ 1868.107473][T12484]      fib6_nh_init+0x3dd/0x15c0
[ 1868.107473][T12484]      nh_create_ipv6+0x377/0x600
[ 1868.107473][T12484]      nexthop_create+0x311/0x650
[ 1868.107473][T12484]      rtm_new_nexthop+0x3f0/0x5c0
[ 1868.107473][T12484]      rtnetlink_rcv_msg+0x2fb/0xc10
[ 1868.107473][T12484]      netlink_rcv_skb+0x130/0x360
[ 1868.107473][T12484]      netlink_unicast+0x449/0x710
[ 1868.107473][T12484]      netlink_sendmsg+0x723/0xbe0
[ 1868.107473][T12484]      ____sys_sendmsg+0x800/0xa90
[ 1868.107473][T12484]      ___sys_sendmsg+0xee/0x170
[ 1868.107473][T12484]      __sys_sendmsg+0xc2/0x150
[ 1868.107473][T12484]      do_syscall_64+0xc1/0x1d0
[ 1868.107473][T12484]      entry_SYSCALL_64_after_hwframe+0x77/0x7f
[ 1868.107473][T12484] 
[ 1868.109800][T12484] ref_tracker: veth_A-R1@ffff8880060c45e0 has 1/4 users at
[ 1868.109800][T12484]      ipv6_add_dev+0x3b9/0x11c0
[ 1868.109800][T12484]      addrconf_notify+0x344/0xd60
[ 1868.109800][T12484]      notifier_call_chain+0xcd/0x150
[ 1868.109800][T12484]      register_netdevice+0x1091/0x1690
[ 1868.109800][T12484]      veth_newlink+0x401/0x830
[ 1868.109800][T12484]      rtnl_newlink_create+0x341/0x850
[ 1868.109800][T12484]      __rtnl_newlink+0xac9/0xd80
[ 1868.109800][T12484]      rtnl_newlink+0x63/0xa0
[ 1868.109800][T12484]      rtnetlink_rcv_msg+0x2fb/0xc10
[ 1868.109800][T12484]      netlink_rcv_skb+0x130/0x360
[ 1868.109800][T12484]      netlink_unicast+0x449/0x710
[ 1868.109800][T12484]      netlink_sendmsg+0x723/0xbe0
[ 1868.109800][T12484]      ____sys_sendmsg+0x800/0xa90
[ 1868.109800][T12484]      ___sys_sendmsg+0xee/0x170
[ 1868.109800][T12484]      __sys_sendmsg+0xc2/0x150
[ 1868.109800][T12484]      do_syscall_64+0xc1/0x1d0
[ 1868.109800][T12484] 
[ 1878.221231][T12484] unregister_netdevice: waiting for veth_A-R1 to become free. Usage count = 5
[ 1878.221630][T12484] ref_tracker: veth_A-R1@ffff8880060c45e0 has 1/4 users at
[ 1878.221630][T12484]      dst_init+0x84/0x4a0
[ 1878.221630][T12484]      dst_alloc+0x97/0x150
[ 1878.221630][T12484]      ip6_dst_alloc+0x23/0x90
[ 1878.221630][T12484]      ip6_rt_pcpu_alloc+0x1e6/0x520
[ 1878.221630][T12484]      ip6_pol_route+0x56f/0x840
[ 1878.221630][T12484]      fib6_rule_lookup+0x334/0x630
[ 1878.221630][T12484]      ip6_route_output_flags+0x259/0x480
[ 1878.221630][T12484]      ip6_dst_lookup_tail.constprop.0+0x700/0xb60
[ 1878.221630][T12484]      ip6_dst_lookup_flow+0x88/0x190
[ 1878.221630][T12484]      udp_tunnel6_dst_lookup+0x2b0/0x4d0
[ 1878.221630][T12484]      vxlan_xmit_one+0xd41/0x4500 [vxlan]
[ 1878.221630][T12484]      vxlan_xmit+0x9b6/0xf10 [vxlan]
[ 1878.221630][T12484]      dev_hard_start_xmit+0x10e/0x360
[ 1878.221630][T12484]      __dev_queue_xmit+0xe76/0x1740
[ 1878.221630][T12484]      arp_solicit+0x4aa/0xe20
[ 1878.221630][T12484]      neigh_probe+0xaa/0xf0
[ 1878.221630][T12484] 
[ 1878.223972][T12484] ref_tracker: veth_A-R1@ffff8880060c45e0 has 1/4 users at
[ 1878.223972][T12484]      dst_init+0x84/0x4a0
[ 1878.223972][T12484]      dst_alloc+0x97/0x150
[ 1878.223972][T12484]      ip6_dst_alloc+0x23/0x90
[ 1878.223972][T12484]      ip6_rt_pcpu_alloc+0x1e6/0x520
[ 1878.223972][T12484]      ip6_pol_route+0x56f/0x840
[ 1878.223972][T12484]      fib6_rule_lookup+0x334/0x630
[ 1878.223972][T12484]      ip6_route_output_flags+0x259/0x480
[ 1878.223972][T12484]      ip6_dst_lookup_tail.constprop.0+0x700/0xb60
[ 1878.223972][T12484]      ip6_dst_lookup_flow+0x88/0x190
[ 1878.223972][T12484]      udp_tunnel6_dst_lookup+0x2b0/0x4d0
[ 1878.223972][T12484]      vxlan_xmit_one+0xd41/0x4500 [vxlan]
[ 1878.223972][T12484]      vxlan_xmit+0x9b6/0xf10 [vxlan]
[ 1878.223972][T12484]      dev_hard_start_xmit+0x10e/0x360
[ 1878.223972][T12484]      __dev_queue_xmit+0xe76/0x1740
[ 1878.223972][T12484]      ip6_finish_output2+0x59b/0xff0
[ 1878.223972][T12484]      ip6_finish_output+0x553/0xdf0
[ 1878.223972][T12484] 
[ 1878.226285][T12484] ref_tracker: veth_A-R1@ffff8880060c45e0 has 1/4 users at
[ 1878.226285][T12484]      netdev_get_by_index+0x5e/0x80
[ 1878.226285][T12484]      fib6_nh_init+0x3dd/0x15c0
[ 1878.226285][T12484]      nh_create_ipv6+0x377/0x600
[ 1878.226285][T12484]      nexthop_create+0x311/0x650
[ 1878.226285][T12484]      rtm_new_nexthop+0x3f0/0x5c0
[ 1878.226285][T12484]      rtnetlink_rcv_msg+0x2fb/0xc10
[ 1878.226285][T12484]      netlink_rcv_skb+0x130/0x360
[ 1878.226285][T12484]      netlink_unicast+0x449/0x710
[ 1878.226285][T12484]      netlink_sendmsg+0x723/0xbe0
[ 1878.226285][T12484]      ____sys_sendmsg+0x800/0xa90
[ 1878.226285][T12484]      ___sys_sendmsg+0xee/0x170
[ 1878.226285][T12484]      __sys_sendmsg+0xc2/0x150
[ 1878.226285][T12484]      do_syscall_64+0xc1/0x1d0
[ 1878.226285][T12484]      entry_SYSCALL_64_after_hwframe+0x77/0x7f
[ 1878.226285][T12484] 
[ 1878.228262][T12484] ref_tracker: veth_A-R1@ffff8880060c45e0 has 1/4 users at
[ 1878.228262][T12484]      ipv6_add_dev+0x3b9/0x11c0
[ 1878.228262][T12484]      addrconf_notify+0x344/0xd60
[ 1878.228262][T12484]      notifier_call_chain+0xcd/0x150
[ 1878.228262][T12484]      register_netdevice+0x1091/0x1690
[ 1878.228262][T12484]      veth_newlink+0x401/0x830
[ 1878.228262][T12484]      rtnl_newlink_create+0x341/0x850
[ 1878.228262][T12484]      __rtnl_newlink+0xac9/0xd80
[ 1878.228262][T12484]      rtnl_newlink+0x63/0xa0
[ 1878.228262][T12484]      rtnetlink_rcv_msg+0x2fb/0xc10
[ 1878.228262][T12484]      netlink_rcv_skb+0x130/0x360
[ 1878.228262][T12484]      netlink_unicast+0x449/0x710
[ 1878.228262][T12484]      netlink_sendmsg+0x723/0xbe0
[ 1878.228262][T12484]      ____sys_sendmsg+0x800/0xa90
[ 1878.228262][T12484]      ___sys_sendmsg+0xee/0x170
[ 1878.228262][T12484]      __sys_sendmsg+0xc2/0x150
[ 1878.228262][T12484]      do_syscall_64+0xc1/0x1d0
[ 1878.228262][T12484] 
[ 1888.397169][T12484] unregister_netdevice: waiting for veth_A-R1 to become free. Usage count = 5
[ 1888.397586][T12484] ref_tracker: veth_A-R1@ffff8880060c45e0 has 1/4 users at
[ 1888.397586][T12484]      dst_init+0x84/0x4a0
[ 1888.397586][T12484]      dst_alloc+0x97/0x150
[ 1888.397586][T12484]      ip6_dst_alloc+0x23/0x90
[ 1888.397586][T12484]      ip6_rt_pcpu_alloc+0x1e6/0x520
[ 1888.397586][T12484]      ip6_pol_route+0x56f/0x840
[ 1888.397586][T12484]      fib6_rule_lookup+0x334/0x630
[ 1888.397586][T12484]      ip6_route_output_flags+0x259/0x480
[ 1888.397586][T12484]      ip6_dst_lookup_tail.constprop.0+0x700/0xb60
[ 1888.397586][T12484]      ip6_dst_lookup_flow+0x88/0x190
[ 1888.397586][T12484]      udp_tunnel6_dst_lookup+0x2b0/0x4d0
[ 1888.397586][T12484]      vxlan_xmit_one+0xd41/0x4500 [vxlan]
[ 1888.397586][T12484]      vxlan_xmit+0x9b6/0xf10 [vxlan]
[ 1888.397586][T12484]      dev_hard_start_xmit+0x10e/0x360
[ 1888.397586][T12484]      __dev_queue_xmit+0xe76/0x1740
[ 1888.397586][T12484]      arp_solicit+0x4aa/0xe20
[ 1888.397586][T12484]      neigh_probe+0xaa/0xf0
[ 1888.397586][T12484] 
[ 1888.400065][T12484] ref_tracker: veth_A-R1@ffff8880060c45e0 has 1/4 users at
[ 1888.400065][T12484]      dst_init+0x84/0x4a0
[ 1888.400065][T12484]      dst_alloc+0x97/0x150
[ 1888.400065][T12484]      ip6_dst_alloc+0x23/0x90
[ 1888.400065][T12484]      ip6_rt_pcpu_alloc+0x1e6/0x520
[ 1888.400065][T12484]      ip6_pol_route+0x56f/0x840
[ 1888.400065][T12484]      fib6_rule_lookup+0x334/0x630
[ 1888.400065][T12484]      ip6_route_output_flags+0x259/0x480
[ 1888.400065][T12484]      ip6_dst_lookup_tail.constprop.0+0x700/0xb60
[ 1888.400065][T12484]      ip6_dst_lookup_flow+0x88/0x190
[ 1888.400065][T12484]      udp_tunnel6_dst_lookup+0x2b0/0x4d0
[ 1888.400065][T12484]      vxlan_xmit_one+0xd41/0x4500 [vxlan]
[ 1888.400065][T12484]      vxlan_xmit+0x9b6/0xf10 [vxlan]
[ 1888.400065][T12484]      dev_hard_start_xmit+0x10e/0x360
[ 1888.400065][T12484]      __dev_queue_xmit+0xe76/0x1740
[ 1888.400065][T12484]      ip6_finish_output2+0x59b/0xff0
[ 1888.400065][T12484]      ip6_finish_output+0x553/0xdf0
[ 1888.400065][T12484] 
[ 1888.402504][T12484] ref_tracker: veth_A-R1@ffff8880060c45e0 has 1/4 users at
[ 1888.402504][T12484]      netdev_get_by_index+0x5e/0x80
[ 1888.402504][T12484]      fib6_nh_init+0x3dd/0x15c0
[ 1888.402504][T12484]      nh_create_ipv6+0x377/0x600
[ 1888.402504][T12484]      nexthop_create+0x311/0x650
[ 1888.402504][T12484]      rtm_new_nexthop+0x3f0/0x5c0
[ 1888.402504][T12484]      rtnetlink_rcv_msg+0x2fb/0xc10
[ 1888.402504][T12484]      netlink_rcv_skb+0x130/0x360
[ 1888.402504][T12484]      netlink_unicast+0x449/0x710
[ 1888.402504][T12484]      netlink_sendmsg+0x723/0xbe0
[ 1888.402504][T12484]      ____sys_sendmsg+0x800/0xa90
[ 1888.402504][T12484]      ___sys_sendmsg+0xee/0x170
[ 1888.402504][T12484]      __sys_sendmsg+0xc2/0x150
[ 1888.402504][T12484]      do_syscall_64+0xc1/0x1d0
[ 1888.402504][T12484]      entry_SYSCALL_64_after_hwframe+0x77/0x7f
[ 1888.402504][T12484] 
[ 1888.404580][T12484] ref_tracker: veth_A-R1@ffff8880060c45e0 has 1/4 users at
[ 1888.404580][T12484]      ipv6_add_dev+0x3b9/0x11c0
[ 1888.404580][T12484]      addrconf_notify+0x344/0xd60
[ 1888.404580][T12484]      notifier_call_chain+0xcd/0x150
[ 1888.404580][T12484]      register_netdevice+0x1091/0x1690
[ 1888.404580][T12484]      veth_newlink+0x401/0x830
[ 1888.404580][T12484]      rtnl_newlink_create+0x341/0x850
[ 1888.404580][T12484]      __rtnl_newlink+0xac9/0xd80
[ 1888.404580][T12484]      rtnl_newlink+0x63/0xa0
[ 1888.404580][T12484]      rtnetlink_rcv_msg+0x2fb/0xc10
[ 1888.404580][T12484]      netlink_rcv_skb+0x130/0x360
[ 1888.404580][T12484]      netlink_unicast+0x449/0x710
[ 1888.404580][T12484]      netlink_sendmsg+0x723/0xbe0
[ 1888.404580][T12484]      ____sys_sendmsg+0x800/0xa90
[ 1888.404580][T12484]      ___sys_sendmsg+0xee/0x170
[ 1888.404580][T12484]      __sys_sendmsg+0xc2/0x150
[ 1888.404580][T12484]      do_syscall_64+0xc1/0x1d0
[ 1888.404580][T12484] 
[ 1898.589197][T12484] unregister_netdevice: waiting for veth_A-R1 to become free. Usage count = 5
[ 1898.589575][T12484] ref_tracker: veth_A-R1@ffff8880060c45e0 has 1/4 users at
[ 1898.589575][T12484]      dst_init+0x84/0x4a0
[ 1898.589575][T12484]      dst_alloc+0x97/0x150
[ 1898.589575][T12484]      ip6_dst_alloc+0x23/0x90
[ 1898.589575][T12484]      ip6_rt_pcpu_alloc+0x1e6/0x520
[ 1898.589575][T12484]      ip6_pol_route+0x56f/0x840
[ 1898.589575][T12484]      fib6_rule_lookup+0x334/0x630
[ 1898.589575][T12484]      ip6_route_output_flags+0x259/0x480
[ 1898.589575][T12484]      ip6_dst_lookup_tail.constprop.0+0x700/0xb60
[ 1898.589575][T12484]      ip6_dst_lookup_flow+0x88/0x190
[ 1898.589575][T12484]      udp_tunnel6_dst_lookup+0x2b0/0x4d0
[ 1898.589575][T12484]      vxlan_xmit_one+0xd41/0x4500 [vxlan]
[ 1898.589575][T12484]      vxlan_xmit+0x9b6/0xf10 [vxlan]
[ 1898.589575][T12484]      dev_hard_start_xmit+0x10e/0x360
[ 1898.589575][T12484]      __dev_queue_xmit+0xe76/0x1740
[ 1898.589575][T12484]      arp_solicit+0x4aa/0xe20
[ 1898.589575][T12484]      neigh_probe+0xaa/0xf0
[ 1898.589575][T12484] 
[ 1898.591877][T12484] ref_tracker: veth_A-R1@ffff8880060c45e0 has 1/4 users at
[ 1898.591877][T12484]      dst_init+0x84/0x4a0
[ 1898.591877][T12484]      dst_alloc+0x97/0x150
[ 1898.591877][T12484]      ip6_dst_alloc+0x23/0x90
[ 1898.591877][T12484]      ip6_rt_pcpu_alloc+0x1e6/0x520
[ 1898.591877][T12484]      ip6_pol_route+0x56f/0x840
[ 1898.591877][T12484]      fib6_rule_lookup+0x334/0x630
[ 1898.591877][T12484]      ip6_route_output_flags+0x259/0x480
[ 1898.591877][T12484]      ip6_dst_lookup_tail.constprop.0+0x700/0xb60
[ 1898.591877][T12484]      ip6_dst_lookup_flow+0x88/0x190
[ 1898.591877][T12484]      udp_tunnel6_dst_lookup+0x2b0/0x4d0
[ 1898.591877][T12484]      vxlan_xmit_one+0xd41/0x4500 [vxlan]
[ 1898.591877][T12484]      vxlan_xmit+0x9b6/0xf10 [vxlan]
[ 1898.591877][T12484]      dev_hard_start_xmit+0x10e/0x360
[ 1898.591877][T12484]      __dev_queue_xmit+0xe76/0x1740
[ 1898.591877][T12484]      ip6_finish_output2+0x59b/0xff0
[ 1898.591877][T12484]      ip6_finish_output+0x553/0xdf0
[ 1898.591877][T12484] 
[ 1898.594146][T12484] ref_tracker: veth_A-R1@ffff8880060c45e0 has 1/4 users at
[ 1898.594146][T12484]      netdev_get_by_index+0x5e/0x80
[ 1898.594146][T12484]      fib6_nh_init+0x3dd/0x15c0
[ 1898.594146][T12484]      nh_create_ipv6+0x377/0x600
[ 1898.594146][T12484]      nexthop_create+0x311/0x650
[ 1898.594146][T12484]      rtm_new_nexthop+0x3f0/0x5c0
[ 1898.594146][T12484]      rtnetlink_rcv_msg+0x2fb/0xc10
[ 1898.594146][T12484]      netlink_rcv_skb+0x130/0x360
[ 1898.594146][T12484]      netlink_unicast+0x449/0x710
[ 1898.594146][T12484]      netlink_sendmsg+0x723/0xbe0
[ 1898.594146][T12484]      ____sys_sendmsg+0x800/0xa90
[ 1898.594146][T12484]      ___sys_sendmsg+0xee/0x170
[ 1898.594146][T12484]      __sys_sendmsg+0xc2/0x150
[ 1898.594146][T12484]      do_syscall_64+0xc1/0x1d0
[ 1898.594146][T12484]      entry_SYSCALL_64_after_hwframe+0x77/0x7f
[ 1898.594146][T12484] 
[ 1898.596102][T12484] ref_tracker: veth_A-R1@ffff8880060c45e0 has 1/4 users at
[ 1898.596102][T12484]      ipv6_add_dev+0x3b9/0x11c0
[ 1898.596102][T12484]      addrconf_notify+0x344/0xd60
[ 1898.596102][T12484]      notifier_call_chain+0xcd/0x150
[ 1898.596102][T12484]      register_netdevice+0x1091/0x1690
[ 1898.596102][T12484]      veth_newlink+0x401/0x830
[ 1898.596102][T12484]      rtnl_newlink_create+0x341/0x850
[ 1898.596102][T12484]      __rtnl_newlink+0xac9/0xd80
[ 1898.596102][T12484]      rtnl_newlink+0x63/0xa0
[ 1898.596102][T12484]      rtnetlink_rcv_msg+0x2fb/0xc10
[ 1898.596102][T12484]      netlink_rcv_skb+0x130/0x360
[ 1898.596102][T12484]      netlink_unicast+0x449/0x710
[ 1898.596102][T12484]      netlink_sendmsg+0x723/0xbe0
[ 1898.596102][T12484]      ____sys_sendmsg+0x800/0xa90
[ 1898.596102][T12484]      ___sys_sendmsg+0xee/0x170
[ 1898.596102][T12484]      __sys_sendmsg+0xc2/0x150
[ 1898.596102][T12484]      do_syscall_64+0xc1/0x1d0
[ 1898.596102][T12484] 
[ 1908.670144][T12484] unregister_netdevice: waiting for veth_A-R1 to become free. Usage count = 5
[ 1908.670561][T12484] ref_tracker: veth_A-R1@ffff8880060c45e0 has 1/4 users at
[ 1908.670561][T12484]      dst_init+0x84/0x4a0
[ 1908.670561][T12484]      dst_alloc+0x97/0x150
[ 1908.670561][T12484]      ip6_dst_alloc+0x23/0x90
[ 1908.670561][T12484]      ip6_rt_pcpu_alloc+0x1e6/0x520
[ 1908.670561][T12484]      ip6_pol_route+0x56f/0x840
[ 1908.670561][T12484]      fib6_rule_lookup+0x334/0x630
[ 1908.670561][T12484]      ip6_route_output_flags+0x259/0x480
[ 1908.670561][T12484]      ip6_dst_lookup_tail.constprop.0+0x700/0xb60
[ 1908.670561][T12484]      ip6_dst_lookup_flow+0x88/0x190
[ 1908.670561][T12484]      udp_tunnel6_dst_lookup+0x2b0/0x4d0
[ 1908.670561][T12484]      vxlan_xmit_one+0xd41/0x4500 [vxlan]
[ 1908.670561][T12484]      vxlan_xmit+0x9b6/0xf10 [vxlan]
[ 1908.670561][T12484]      dev_hard_start_xmit+0x10e/0x360
[ 1908.670561][T12484]      __dev_queue_xmit+0xe76/0x1740
[ 1908.670561][T12484]      arp_solicit+0x4aa/0xe20
[ 1908.670561][T12484]      neigh_probe+0xaa/0xf0
[ 1908.670561][T12484] 
[ 1908.673046][T12484] ref_tracker: veth_A-R1@ffff8880060c45e0 has 1/4 users at
[ 1908.673046][T12484]      dst_init+0x84/0x4a0
[ 1908.673046][T12484]      dst_alloc+0x97/0x150
[ 1908.673046][T12484]      ip6_dst_alloc+0x23/0x90
[ 1908.673046][T12484]      ip6_rt_pcpu_alloc+0x1e6/0x520
[ 1908.673046][T12484]      ip6_pol_route+0x56f/0x840
[ 1908.673046][T12484]      fib6_rule_lookup+0x334/0x630
[ 1908.673046][T12484]      ip6_route_output_flags+0x259/0x480
[ 1908.673046][T12484]      ip6_dst_lookup_tail.constprop.0+0x700/0xb60
[ 1908.673046][T12484]      ip6_dst_lookup_flow+0x88/0x190
[ 1908.673046][T12484]      udp_tunnel6_dst_lookup+0x2b0/0x4d0
[ 1908.673046][T12484]      vxlan_xmit_one+0xd41/0x4500 [vxlan]
[ 1908.673046][T12484]      vxlan_xmit+0x9b6/0xf10 [vxlan]
[ 1908.673046][T12484]      dev_hard_start_xmit+0x10e/0x360
[ 1908.673046][T12484]      __dev_queue_xmit+0xe76/0x1740
[ 1908.673046][T12484]      ip6_finish_output2+0x59b/0xff0
[ 1908.673046][T12484]      ip6_finish_output+0x553/0xdf0
[ 1908.673046][T12484] 
[ 1908.675506][T12484] ref_tracker: veth_A-R1@ffff8880060c45e0 has 1/4 users at
[ 1908.675506][T12484]      netdev_get_by_index+0x5e/0x80
[ 1908.675506][T12484]      fib6_nh_init+0x3dd/0x15c0
[ 1908.675506][T12484]      nh_create_ipv6+0x377/0x600
[ 1908.675506][T12484]      nexthop_create+0x311/0x650
[ 1908.675506][T12484]      rtm_new_nexthop+0x3f0/0x5c0
[ 1908.675506][T12484]      rtnetlink_rcv_msg+0x2fb/0xc10
[ 1908.675506][T12484]      netlink_rcv_skb+0x130/0x360
[ 1908.675506][T12484]      netlink_unicast+0x449/0x710
[ 1908.675506][T12484]      netlink_sendmsg+0x723/0xbe0
[ 1908.675506][T12484]      ____sys_sendmsg+0x800/0xa90
[ 1908.675506][T12484]      ___sys_sendmsg+0xee/0x170
[ 1908.675506][T12484]      __sys_sendmsg+0xc2/0x150
[ 1908.675506][T12484]      do_syscall_64+0xc1/0x1d0
[ 1908.675506][T12484]      entry_SYSCALL_64_after_hwframe+0x77/0x7f
[ 1908.675506][T12484] 
[ 1908.677622][T12484] ref_tracker: veth_A-R1@ffff8880060c45e0 has 1/4 users at
[ 1908.677622][T12484]      ipv6_add_dev+0x3b9/0x11c0
[ 1908.677622][T12484]      addrconf_notify+0x344/0xd60
[ 1908.677622][T12484]      notifier_call_chain+0xcd/0x150
[ 1908.677622][T12484]      register_netdevice+0x1091/0x1690
[ 1908.677622][T12484]      veth_newlink+0x401/0x830
[ 1908.677622][T12484]      rtnl_newlink_create+0x341/0x850
[ 1908.677622][T12484]      __rtnl_newlink+0xac9/0xd80
[ 1908.677622][T12484]      rtnl_newlink+0x63/0xa0
[ 1908.677622][T12484]      rtnetlink_rcv_msg+0x2fb/0xc10
[ 1908.677622][T12484]      netlink_rcv_skb+0x130/0x360
[ 1908.677622][T12484]      netlink_unicast+0x449/0x710
[ 1908.677622][T12484]      netlink_sendmsg+0x723/0xbe0
[ 1908.677622][T12484]      ____sys_sendmsg+0x800/0xa90
[ 1908.677622][T12484]      ___sys_sendmsg+0xee/0x170
[ 1908.677622][T12484]      __sys_sendmsg+0xc2/0x150
[ 1908.677622][T12484]      do_syscall_64+0xc1/0x1d0
[ 1908.677622][T12484] 
"""

    hung_task = """
[ 1863.157993][ T9043] br0: port 1(vx0) entered forwarding state
[ 2090.392704][   T43] INFO: task tc:9091 blocked for more than 122 seconds.
[ 2090.393146][   T43]       Not tainted 6.10.0-virtme #1
[ 2090.393327][   T43] "echo 0 > /proc/sys/kernel/hung_task_timeout_secs" disables this message.
[ 2090.393598][   T43] task:tc              state:D stack:26464 pid:9091  tgid:9091  ppid:9090   flags:0x00000000
[ 2090.393857][   T43] Call Trace:
[ 2090.393956][   T43]  <TASK>
[ 2090.394033][   T43]  __schedule+0x6e0/0x17e0
[ 2090.394184][   T43]  ? __pfx___schedule+0x10/0x10
[ 2090.394318][   T43]  ? schedule+0x1a5/0x210
[ 2090.394420][   T43]  ? __pfx_lock_acquire.part.0+0x10/0x10
[ 2090.394562][   T43]  ? trace_lock_acquire+0x14d/0x1f0
[ 2090.394701][   T43]  ? schedule+0x1a5/0x210
[ 2090.394800][   T43]  schedule+0xdf/0x210
[ 2090.395240][   T43]  d_alloc_parallel+0xaef/0xed0
[ 2090.395379][   T43]  ? __pfx_d_alloc_parallel+0x10/0x10
[ 2090.395505][   T43]  ? __pfx_default_wake_function+0x10/0x10
[ 2090.395676][   T43]  ? lockdep_init_map_type+0x2cb/0x7c0
[ 2090.395809][   T43]  __lookup_slow+0x17f/0x3c0
[ 2090.395942][   T43]  ? __pfx___lookup_slow+0x10/0x10
[ 2090.396075][   T43]  ? walk_component+0x29e/0x4f0
[ 2090.396219][   T43]  walk_component+0x2ab/0x4f0
[ 2090.396350][   T43]  link_path_walk.part.0.constprop.0+0x416/0x940
[ 2090.396517][   T43]  ? __pfx_link_path_walk.part.0.constprop.0+0x10/0x10
[ 2090.396706][   T43]  path_lookupat+0x72/0x660
[ 2090.396832][   T43]  filename_lookup+0x19e/0x420
[ 2090.396958][   T43]  ? __pfx_filename_lookup+0x10/0x10
[ 2090.397090][   T43]  ? find_held_lock+0x2c/0x110
[ 2090.397213][   T43]  ? __lock_release+0x103/0x460
[ 2090.397335][   T43]  ? __pfx___lock_release+0x10/0x10
[ 2090.397456][   T43]  ? trace_lock_acquire+0x14d/0x1f0
[ 2090.397590][   T43]  ? __might_fault+0xc3/0x170
[ 2090.397720][   T43]  ? lock_acquire+0x32/0xc0
[ 2090.397838][   T43]  ? __might_fault+0xc3/0x170
[ 2090.397966][   T43]  vfs_statx+0xbf/0x140
[ 2090.398060][   T43]  ? __pfx_vfs_statx+0x10/0x10
[ 2090.398183][   T43]  ? getname_flags+0xb3/0x410
[ 2090.398307][   T43]  vfs_fstatat+0x80/0xc0
[ 2090.398400][   T43]  __do_sys_newfstatat+0x75/0xd0
[ 2090.398548][   T43]  ? __pfx___do_sys_newfstatat+0x10/0x10
[ 2090.398669][   T43]  ? user_path_at+0x45/0x60
[ 2090.398802][   T43]  ? __x64_sys_openat+0x123/0x1e0
[ 2090.398929][   T43]  ? __pfx___x64_sys_openat+0x10/0x10
[ 2090.399052][   T43]  ? __pfx_do_faccessat+0x10/0x10
[ 2090.399179][   T43]  ? lockdep_hardirqs_on_prepare+0x275/0x410
[ 2090.399327][   T43]  do_syscall_64+0xc1/0x1d0
[ 2090.399451][   T43]  entry_SYSCALL_64_after_hwframe+0x77/0x7f
[ 2090.399612][   T43] RIP: 0033:0x7fef39aaceae
[ 2090.399746][   T43] RSP: 002b:00007ffc38865528 EFLAGS: 00000246 ORIG_RAX: 0000000000000106
[ 2090.399969][   T43] RAX: ffffffffffffffda RBX: 0000000000000004 RCX: 00007fef39aaceae
[ 2090.400180][   T43] RDX: 00007ffc38865600 RSI: 00007ffc38865530 RDI: 00000000ffffff9c
[ 2090.400371][   T43] RBP: 00007ffc388656c0 R08: 00000000ffffffff R09: 00007ffc38865530
[ 2090.400563][   T43] R10: 0000000000000000 R11: 0000000000000246 R12: 00007ffc38865537
[ 2090.400745][   T43] R13: 00007ffc38865530 R14: 00007fef39abc220 R15: 00007fef39a7e000
[ 2090.400949][   T43]  </TASK>
[ 2090.401044][   T43] INFO: task jq:9092 blocked for more than 122 seconds.
[ 2090.401211][   T43]       Not tainted 6.10.0-virtme #1
[ 2090.401326][   T43] "echo 0 > /proc/sys/kernel/hung_task_timeout_secs" disables this message.
[ 2090.401539][   T43] task:jq              state:D stack:26464 pid:9092  tgid:9092  ppid:9090   flags:0x00004000
[ 2090.401808][   T43] Call Trace:
[ 2090.401901][   T43]  <TASK>
[ 2090.401968][   T43]  __schedule+0x6e0/0x17e0
[ 2090.402124][   T43]  ? __pfx___schedule+0x10/0x10
[ 2090.402243][   T43]  ? schedule+0x1a5/0x210
[ 2090.402338][   T43]  ? __pfx_lock_acquire.part.0+0x10/0x10
[ 2090.402477][   T43]  ? trace_lock_acquire+0x14d/0x1f0
[ 2090.402626][   T43]  ? schedule+0x1a5/0x210
[ 2090.402731][   T43]  schedule+0xdf/0x210
[ 2090.402824][   T43]  __wait_on_freeing_inode+0x115/0x280
[ 2090.402982][   T43]  ? __pfx___wait_on_freeing_inode+0x10/0x10
[ 2090.403171][   T43]  ? __pfx_wake_bit_function+0x10/0x10
[ 2090.403329][   T43]  ? lock_acquire+0x32/0xc0
[ 2090.403450][   T43]  ? find_inode_fast+0x158/0x450
[ 2090.403605][   T43]  find_inode_fast+0x18d/0x450
[ 2090.403742][   T43]  iget_locked+0x7d/0x390
[ 2090.403834][   T43]  ? hlock_class+0x4e/0x130
[ 2090.403972][   T43]  v9fs_fid_iget_dotl+0x78/0x2d0
[ 2090.404117][   T43]  v9fs_vfs_lookup.part.0+0x1ed/0x390
[ 2090.404263][   T43]  ? __pfx_v9fs_vfs_lookup.part.0+0x10/0x10
[ 2090.404417][   T43]  ? lockdep_init_map_type+0x2cb/0x7c0
[ 2090.404589][   T43]  __lookup_slow+0x209/0x3c0
[ 2090.404723][   T43]  ? __pfx___lookup_slow+0x10/0x10
[ 2090.404854][   T43]  ? walk_component+0x29e/0x4f0
[ 2090.405009][   T43]  walk_component+0x2ab/0x4f0
[ 2090.405137][   T43]  link_path_walk.part.0.constprop.0+0x416/0x940
[ 2090.405312][   T43]  ? __pfx_link_path_walk.part.0.constprop.0+0x10/0x10
[ 2090.405474][   T43]  path_openat+0x1be/0x440
[ 2090.405608][   T43]  ? __pfx_path_openat+0x10/0x10
[ 2090.405756][   T43]  ? __lock_acquire+0xaf0/0x1570
[ 2090.405883][   T43]  do_filp_open+0x1b3/0x3e0
[ 2090.406008][   T43]  ? __pfx_do_filp_open+0x10/0x10
[ 2090.406162][   T43]  ? find_held_lock+0x2c/0x110
[ 2090.406294][   T43]  ? do_raw_spin_lock+0x131/0x270
[ 2090.406442][   T43]  ? __pfx_do_raw_spin_lock+0x10/0x10
[ 2090.406589][   T43]  ? alloc_fd+0x1f5/0x650
[ 2090.406693][   T43]  ? do_raw_spin_unlock+0x58/0x220
[ 2090.406815][   T43]  ? _raw_spin_unlock+0x23/0x40
[ 2090.406954][   T43]  ? alloc_fd+0x1f5/0x650
[ 2090.407075][   T43]  do_sys_openat2+0x122/0x160
[ 2090.407208][   T43]  ? __pfx_do_sys_openat2+0x10/0x10
[ 2090.407332][   T43]  ? user_path_at+0x45/0x60
[ 2090.407490][   T43]  __x64_sys_openat+0x123/0x1e0
[ 2090.407635][   T43]  ? __pfx___x64_sys_openat+0x10/0x10
[ 2090.407764][   T43]  ? __pfx_do_faccessat+0x10/0x10
[ 2090.407950][   T43]  do_syscall_64+0xc1/0x1d0
[ 2090.408091][   T43]  entry_SYSCALL_64_after_hwframe+0x77/0x7f
[ 2090.408251][   T43] RIP: 0033:0x7f7b9086b0e8
[ 2090.408406][   T43] RSP: 002b:00007fff468c5918 EFLAGS: 00000287 ORIG_RAX: 0000000000000101
[ 2090.408637][   T43] RAX: ffffffffffffffda RBX: 00007fff468c5b9f RCX: 00007f7b9086b0e8
[ 2090.408838][   T43] RDX: 0000000000080000 RSI: 00007fff468c5990 RDI: 00000000ffffff9c
[ 2090.409040][   T43] RBP: 00007fff468c5980 R08: 0000000000080000 R09: 00007fff468c5990
[ 2090.409220][   T43] R10: 0000000000000000 R11: 0000000000000287 R12: 00007fff468c5997
[ 2090.409439][   T43] R13: 00007fff468c5bb0 R14: 00007fff468c5990 R15: 00007f7b9083c000
[ 2090.409652][   T43]  </TASK>
[ 2090.409788][   T43] 
[ 2090.409788][   T43] Showing all locks held in the system:
[ 2090.409977][   T43] 1 lock held by khungtaskd/43:
[ 2090.410101][   T43]  #0: ffffffffb9368c00 (rcu_read_lock){....}-{1:2}, at: debug_show_all_locks+0x70/0x3a0
[ 2090.410350][   T43] 1 lock held by tc/9091:
[ 2090.410439][   T43]  #0: ffff888001720148 (&type->i_mutex_dir_key#3){++++}-{3:3}, at: walk_component+0x29e/0x4f0
[ 2090.410714][   T43] 1 lock held by jq/9092:
[ 2090.410814][   T43]  #0: ffff888001720148 (&type->i_mutex_dir_key#3){++++}-{3:3}, at: walk_component+0x29e/0x4f0
[ 2090.411085][   T43] 
[ 2090.411150][   T43] =============================================
[ 2090.411150][   T43] 
[ 2213.272644][   T43] INFO: task tc:9091 blocked for more than 245 seconds.
[ 2213.272927][   T43]       Not tainted 6.10.0-virtme #1
[ 2213.273055][   T43] "echo 0 > /proc/sys/kernel/hung_task_timeout_secs" disables this message.
[ 2213.273269][   T43] task:tc              state:D stack:26464 pid:9091  tgid:9091  ppid:9090   flags:0x00000000
[ 2213.273565][   T43] Call Trace:
[ 2213.273667][   T43]  <TASK>
[ 2213.273738][   T43]  __schedule+0x6e0/0x17e0
[ 2213.273875][   T43]  ? __pfx___schedule+0x10/0x10
[ 2213.274005][   T43]  ? schedule+0x1a5/0x210
[ 2213.274109][   T43]  ? __pfx_lock_acquire.part.0+0x10/0x10
[ 2213.274250][   T43]  ? trace_lock_acquire+0x14d/0x1f0
[ 2213.274387][   T43]  ? schedule+0x1a5/0x210
[ 2213.274486][   T43]  schedule+0xdf/0x210
[ 2213.274603][   T43]  d_alloc_parallel+0xaef/0xed0
[ 2213.274757][   T43]  ? __pfx_d_alloc_parallel+0x10/0x10
[ 2213.274882][   T43]  ? __pfx_default_wake_function+0x10/0x10
[ 2213.275038][   T43]  ? lockdep_init_map_type+0x2cb/0x7c0
[ 2213.275175][   T43]  __lookup_slow+0x17f/0x3c0
[ 2213.275305][   T43]  ? __pfx___lookup_slow+0x10/0x10
[ 2213.275438][   T43]  ? walk_component+0x29e/0x4f0
[ 2213.275597][   T43]  walk_component+0x2ab/0x4f0
[ 2213.275749][   T43]  link_path_walk.part.0.constprop.0+0x416/0x940
[ 2213.275908][   T43]  ? __pfx_link_path_walk.part.0.constprop.0+0x10/0x10
[ 2213.276067][   T43]  path_lookupat+0x72/0x660
[ 2213.276193][   T43]  filename_lookup+0x19e/0x420
[ 2213.276317][   T43]  ? __pfx_filename_lookup+0x10/0x10
[ 2213.276454][   T43]  ? find_held_lock+0x2c/0x110
[ 2213.276596][   T43]  ? __lock_release+0x103/0x460
[ 2213.276723][   T43]  ? __pfx___lock_release+0x10/0x10
[ 2213.276850][   T43]  ? trace_lock_acquire+0x14d/0x1f0
[ 2213.276984][   T43]  ? __might_fault+0xc3/0x170
[ 2213.277110][   T43]  ? lock_acquire+0x32/0xc0
[ 2213.277230][   T43]  ? __might_fault+0xc3/0x170
[ 2213.277356][   T43]  vfs_statx+0xbf/0x140
[ 2213.277457][   T43]  ? __pfx_vfs_statx+0x10/0x10
[ 2213.277617][   T43]  ? getname_flags+0xb3/0x410
[ 2213.277748][   T43]  vfs_fstatat+0x80/0xc0
[ 2213.277850][   T43]  __do_sys_newfstatat+0x75/0xd0
[ 2213.277982][   T43]  ? __pfx___do_sys_newfstatat+0x10/0x10
[ 2213.278105][   T43]  ? user_path_at+0x45/0x60
[ 2213.278239][   T43]  ? __x64_sys_openat+0x123/0x1e0
[ 2213.278363][   T43]  ? __pfx___x64_sys_openat+0x10/0x10
[ 2213.278483][   T43]  ? __pfx_do_faccessat+0x10/0x10
[ 2213.278627][   T43]  ? lockdep_hardirqs_on_prepare+0x275/0x410
[ 2213.278779][   T43]  do_syscall_64+0xc1/0x1d0
[ 2213.278921][   T43]  entry_SYSCALL_64_after_hwframe+0x77/0x7f
[ 2213.279083][   T43] RIP: 0033:0x7fef39aaceae
[ 2213.279221][   T43] RSP: 002b:00007ffc38865528 EFLAGS: 00000246 ORIG_RAX: 0000000000000106
[ 2213.279415][   T43] RAX: ffffffffffffffda RBX: 0000000000000004 RCX: 00007fef39aaceae
[ 2213.279615][   T43] RDX: 00007ffc38865600 RSI: 00007ffc38865530 RDI: 00000000ffffff9c
[ 2213.279801][   T43] RBP: 00007ffc388656c0 R08: 00000000ffffffff R09: 00007ffc38865530
[ 2213.279989][   T43] R10: 0000000000000000 R11: 0000000000000246 R12: 00007ffc38865537
[ 2213.280184][   T43] R13: 00007ffc38865530 R14: 00007fef39abc220 R15: 00007fef39a7e000
[ 2213.280377][   T43]  </TASK>
[ 2213.280470][   T43] INFO: task jq:9092 blocked for more than 245 seconds.
[ 2213.280628][   T43]       Not tainted 6.10.0-virtme #1
[ 2213.280743][   T43] "echo 0 > /proc/sys/kernel/hung_task_timeout_secs" disables this message.
[ 2213.280955][   T43] task:jq              state:D stack:26464 pid:9092  tgid:9092  ppid:9090   flags:0x00004000
[ 2213.281199][   T43] Call Trace:
[ 2213.281296][   T43]  <TASK>
[ 2213.281364][   T43]  __schedule+0x6e0/0x17e0
[ 2213.281495][   T43]  ? __pfx___schedule+0x10/0x10
[ 2213.281642][   T43]  ? schedule+0x1a5/0x210
[ 2213.281737][   T43]  ? __pfx_lock_acquire.part.0+0x10/0x10
[ 2213.281868][   T43]  ? trace_lock_acquire+0x14d/0x1f0
[ 2213.282003][   T43]  ? schedule+0x1a5/0x210
[ 2213.282109][   T43]  schedule+0xdf/0x210
[ 2213.282219][   T43]  __wait_on_freeing_inode+0x115/0x280
[ 2213.282350][   T43]  ? __pfx___wait_on_freeing_inode+0x10/0x10
[ 2213.282505][   T43]  ? __pfx_wake_bit_function+0x10/0x10
[ 2213.282650][   T43]  ? lock_acquire+0x32/0xc0
[ 2213.282775][   T43]  ? find_inode_fast+0x158/0x450
[ 2213.282903][   T43]  find_inode_fast+0x18d/0x450
[ 2213.283054][   T43]  iget_locked+0x7d/0x390
[ 2213.283149][   T43]  ? hlock_class+0x4e/0x130
[ 2213.283280][   T43]  v9fs_fid_iget_dotl+0x78/0x2d0
[ 2213.283410][   T43]  v9fs_vfs_lookup.part.0+0x1ed/0x390
[ 2213.283539][   T43]  ? __pfx_v9fs_vfs_lookup.part.0+0x10/0x10
[ 2213.283705][   T43]  ? lockdep_init_map_type+0x2cb/0x7c0
[ 2213.283835][   T43]  __lookup_slow+0x209/0x3c0
[ 2213.283959][   T43]  ? __pfx___lookup_slow+0x10/0x10
[ 2213.284091][   T43]  ? walk_component+0x29e/0x4f0
[ 2213.284232][   T43]  walk_component+0x2ab/0x4f0
[ 2213.284356][   T43]  link_path_walk.part.0.constprop.0+0x416/0x940
[ 2213.284510][   T43]  ? __pfx_link_path_walk.part.0.constprop.0+0x10/0x10
[ 2213.284680][   T43]  path_openat+0x1be/0x440
[ 2213.284804][   T43]  ? __pfx_path_openat+0x10/0x10
[ 2213.284926][   T43]  ? __lock_acquire+0xaf0/0x1570
[ 2213.285051][   T43]  do_filp_open+0x1b3/0x3e0
[ 2213.285180][   T43]  ? __pfx_do_filp_open+0x10/0x10
[ 2213.285321][   T43]  ? find_held_lock+0x2c/0x110
[ 2213.285455][   T43]  ? do_raw_spin_lock+0x131/0x270
[ 2213.285599][   T43]  ? __pfx_do_raw_spin_lock+0x10/0x10
[ 2213.285735][   T43]  ? alloc_fd+0x1f5/0x650
[ 2213.285834][   T43]  ? do_raw_spin_unlock+0x58/0x220
[ 2213.285964][   T43]  ? _raw_spin_unlock+0x23/0x40
[ 2213.286086][   T43]  ? alloc_fd+0x1f5/0x650
[ 2213.286188][   T43]  do_sys_openat2+0x122/0x160
[ 2213.286320][   T43]  ? __pfx_do_sys_openat2+0x10/0x10
[ 2213.286446][   T43]  ? user_path_at+0x45/0x60
[ 2213.286586][   T43]  __x64_sys_openat+0x123/0x1e0
[ 2213.286709][   T43]  ? __pfx___x64_sys_openat+0x10/0x10
[ 2213.286829][   T43]  ? __pfx_do_faccessat+0x10/0x10
[ 2213.286972][   T43]  do_syscall_64+0xc1/0x1d0
[ 2213.287100][   T43]  entry_SYSCALL_64_after_hwframe+0x77/0x7f
[ 2213.287251][   T43] RIP: 0033:0x7f7b9086b0e8
[ 2213.287381][   T43] RSP: 002b:00007fff468c5918 EFLAGS: 00000287 ORIG_RAX: 0000000000000101
[ 2213.287577][   T43] RAX: ffffffffffffffda RBX: 00007fff468c5b9f RCX: 00007f7b9086b0e8
[ 2213.287758][   T43] RDX: 0000000000080000 RSI: 00007fff468c5990 RDI: 00000000ffffff9c
[ 2213.287935][   T43] RBP: 00007fff468c5980 R08: 0000000000080000 R09: 00007fff468c5990
[ 2213.288117][   T43] R10: 0000000000000000 R11: 0000000000000287 R12: 00007fff468c5997
[ 2213.288318][   T43] R13: 00007fff468c5bb0 R14: 00007fff468c5990 R15: 00007f7b9083c000
[ 2213.288514][   T43]  </TASK>
[ 2213.288614][   T43] 
[ 2213.288614][   T43] Showing all locks held in the system:
[ 2213.288798][   T43] 1 lock held by khungtaskd/43:
[ 2213.288925][   T43]  #0: ffffffffb9368c00 (rcu_read_lock){....}-{1:2}, at: debug_show_all_locks+0x70/0x3a0
[ 2213.289157][   T43] 1 lock held by tc/9091:
[ 2213.289251][   T43]  #0: ffff888001720148 (&type->i_mutex_dir_key#3){++++}-{3:3}, at: walk_component+0x29e/0x4f0
[ 2213.289500][   T43] 1 lock held by jq/9092:
[ 2213.289611][   T43]  #0: ffff888001720148 (&type->i_mutex_dir_key#3){++++}-{3:3}, at: walk_component+0x29e/0x4f0
[ 2213.289855][   T43] 
[ 2213.289919][   T43] =============================================
[ 2213.289919][   T43] 
"""


if __name__ == "__main__":
    unittest.main()
