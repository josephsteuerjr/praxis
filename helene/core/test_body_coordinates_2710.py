"""Read-window receipts cannot turn unverified local coordinates into click points."""
import unittest
import body_client


class WindowCoordinates(unittest.TestCase):
    def test_tree_and_flat_unverified_origins_keep_geometry_without_click_center(self):
        for shape in ('tree','flat'):
            node={'id':1,'role':'button','name':'Save','rect':{
                'left':10,'top':20,'width':10,'height':10,'center':{'x':15,'y':25},
                'coordinate_space':'reported_origin_unverified'}}
            receipt={'ok':True,'shape':shape,**({'root':node} if shape=='tree' else {'items':[node]})}
            text=body_client.format_window_read(receipt)
            self.assertNotIn('@15,25',text)
            self.assertIn('rect=10,20+10x10',text)
            self.assertIn('координаты экрана не подтверждены',text)
            self.assertIn('reported_origin_unverified',text)

    def test_verified_screen_and_legacy_receipts_retain_click_center(self):
        for space in (None,'screen'):
            rect={'center':{'x':15,'y':25}}
            if space is not None:rect['coordinate_space']=space
            self.assertIn('@15,25',body_client.format_window_read({'ok':True,'root':{'id':1,'rect':rect}}))


if __name__=='__main__':unittest.main()
